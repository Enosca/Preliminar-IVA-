import streamlit as st
import pandas as pd
import sqlite3
import pdfplumber

# Configuración de la página
st.set_page_config(page_title="Sistema de Liquidación de IVA y Clientes", layout="wide")

# ---------------------------------------------------------
# Conexión a Base de Datos (SQLite)
# ---------------------------------------------------------
CONN = sqlite3.connect("iva_datos.db", check_same_thread=False)

def inicializar_db():
    cursor = CONN.cursor()
    # Modificación/Creación de tablas con columnas de nombre y cuit
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            periodo TEXT,
            comprobante TEXT,
            nombre TEXT,
            cuit TEXT,
            neto REAL,
            iva REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS compras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            periodo TEXT,
            comprobante TEXT,
            nombre TEXT,
            cuit TEXT,
            neto REAL,
            iva REAL
        )
    """)
    
    # Asegurar que las columnas existan si la DB ya estaba creada previamente
    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN nombre TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE ventas ADD COLUMN cuit TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE compras ADD COLUMN nombre TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE compras ADD COLUMN cuit TEXT")
    except: pass

    CONN.commit()

inicializar_db()

# ---------------------------------------------------------
# Procesador Avanzado de AFIP y Archivos Generales
# ---------------------------------------------------------
def limpiar_numero_arg(val):
    if pd.isna(val): return 0.0
    if isinstance(val, (int, float)): return float(val)
    val_str = str(val).replace('.', '').replace(',', '.').strip()
    try:
        return float(val_str)
    except:
        return 0.0

def extraer_periodo(fecha_str):
    try:
        fecha_dt = pd.to_datetime(fecha_str, errors='coerce')
        if pd.notna(fecha_dt):
            return fecha_dt.strftime('%Y-%m')
    except:
        pass
    return "Sin Período"

def procesar_archivo_afip(file, alicuota_def=21.0):
    df = pd.DataFrame()

    if file.name.endswith('.csv'):
        try:
            df = pd.read_csv(file, sep=';', encoding='utf-8')
            if len(df.columns) <= 1:
                file.seek(0)
                df = pd.read_csv(file, sep=',', encoding='utf-8')
        except:
            file.seek(0)
            df = pd.read_csv(file, sep=';', encoding='latin1')

    elif file.name.endswith(('.xlsx', '.xls')):
        df = pd.read_excel(file)

    elif file.name.endswith('.pdf'):
        filas = []
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if row and any(row):
                            filas.append(row)
        if filas:
            df = pd.DataFrame(filas[1:], columns=filas[0])

    if df.empty:
        return None

    df.columns = [str(col).strip() for col in df.columns]

    # --- Detección Específica para Archivos AFIP ---
    col_neto_afip = 'Imp. Neto Gravado Total'
    col_iva_afip = 'Total IVA'
    col_fecha_afip = 'Fecha de Emisión'
    col_tipo_afip = 'Tipo de Comprobante'
    col_comp_afip = 'Número Desde'
    
    # Nombre y CUIT en AFIP (Emitidos vs Recibidos)
    col_nombre_afip = next((c for c in df.columns if 'Denominación' in c or 'Denominacion' in c or 'Nombre' in c or 'Razon Social' in c), None)
    col_cuit_afip = next((c for c in df.columns if 'Nro. Doc' in c or 'CUIT' in c or 'Cuit' in c), None)

    df_resumen = pd.DataFrame()

    CODIGOS_NC = [3, 8, 13, 53]

    def es_nota_de_credito(val_tipo):
        if pd.isna(val_tipo): return False
        try:
            if int(val_tipo) in CODIGOS_NC:
                return True
        except:
            pass
        val_str = str(val_tipo).lower()
        return ('nota de crédito' in val_str or 'nota de credito' in val_str or 'nc' in val_str)

    if col_neto_afip in df.columns and col_iva_afip in df.columns:
        neto_abs = df[col_neto_afip].apply(limpiar_numero_arg)
        iva_abs = df[col_iva_afip].apply(limpiar_numero_arg)

        if col_tipo_afip in df.columns:
            m_nc = df[col_tipo_afip].apply(es_nota_de_credito)
        else:
            m_nc = pd.Series([False] * len(df))

        signo = m_nc.apply(lambda x: -1.0 if x else 1.0)
        df_resumen['neto'] = neto_abs * signo
        df_resumen['iva'] = iva_abs * signo

        df_resumen['fecha'] = df[col_fecha_afip].astype(str) if col_fecha_afip in df.columns else "Sin Fecha"
        df_resumen['periodo'] = df_resumen['fecha'].apply(extraer_periodo)

        df_resumen['nombre'] = df[col_nombre_afip].astype(str).fillna("Consumidor Final / S.D.") if col_nombre_afip else "S.D."
        df_resumen['cuit'] = df[col_cuit_afip].astype(str).fillna("S.D.") if col_cuit_afip else "S.D."

        if 'Punto de Venta' in df.columns and col_comp_afip in df.columns:
            comp_num = df['Punto de Venta'].astype(str) + "-" + df[col_comp_afip].astype(str)
        else:
            comp_num = df[col_comp_afip].astype(str) if col_comp_afip in df.columns else "Sin Comp."

        df_resumen['comprobante'] = comp_num.mask(m_nc, "NC " + comp_num)

    else:
        cols_lower = [c.lower() for c in df.columns]
        c_neto = next((df.columns[i] for i, c in enumerate(cols_lower) if 'neto' in c or 'monto' in c or 'base' in c), None)
        c_iva = next((df.columns[i] for i, c in enumerate(cols_lower) if 'iva' in c or 'debito' in c or 'credito' in c), None)
        c_fecha = next((df.columns[i] for i, c in enumerate(cols_lower) if 'fech' in c or 'date' in c), None)
        c_comp = next((df.columns[i] for i, c in enumerate(cols_lower) if 'comp' in c or 'num' in c or 'factura' in c), None)
        c_nom = next((df.columns[i] for i, c in enumerate(cols_lower) if 'nombre' in c or 'cliente' in c or 'proveedor' in c or 'denominacion' in c or 'razon' in c), None)
        c_cuit = next((df.columns[i] for i, c in enumerate(cols_lower) if 'cuit' in c or 'doc' in c), None)

        if not c_neto:
            num_cols = df.select_dtypes(include=['number']).columns
            if len(num_cols) > 0:
                c_neto = num_cols[0]

        if not c_neto:
            return None

        c_tipo = next((df.columns[i] for i, c in enumerate(cols_lower) if 'tipo' in c or 'comprobante' in c), None)
        if c_tipo:
            m_nc = df[c_tipo].apply(es_nota_de_credito)
        elif c_comp:
            m_nc = df[c_comp].apply(es_nota_de_credito)
        else:
            m_nc = pd.Series([False] * len(df))

        signo = m_nc.apply(lambda x: -1.0 if x else 1.0)
        neto_abs = df[c_neto].apply(limpiar_numero_arg)
        df_resumen['neto'] = neto_abs * signo

        if c_iva:
            iva_abs = df[c_iva].apply(limpiar_numero_arg)
            df_resumen['iva'] = iva_abs * signo
        else:
            df_resumen['iva'] = df_resumen['neto'] * (alicuota_def / 100.0)

        df_resumen['fecha'] = df[c_fecha].astype(str) if c_fecha else "Sin Fecha"
        df_resumen['periodo'] = df_resumen['fecha'].apply(extraer_periodo)
        df_resumen['comprobante'] = df[c_comp].astype(str) if c_comp else "Carga Masiva"
        df_resumen['nombre'] = df[c_nom].astype(str) if c_nom else "S.D."
        df_resumen['cuit'] = df[c_cuit].astype(str) if c_cuit else "S.D."

    return df_resumen[['fecha', 'periodo', 'comprobante', 'nombre', 'cuit', 'neto', 'iva']]

def obtener_datos(tabla):
    return pd.read_sql_query(f"SELECT * FROM {tabla}", CONN)

def guardar_dataframe(tabla, df_para_guardar):
    df_para_guardar.to_sql(tabla, CONN, if_exists='append', index=False)

def limpiar_tabla(tabla):
    cursor = CONN.cursor()
    cursor.execute(f"DELETE FROM {tabla}")
    CONN.commit()

# ---------------------------------------------------------
# Control de Acceso (Barra Lateral)
# ---------------------------------------------------------
st.sidebar.title("🔐 Acceso")
rol = st.sidebar.radio("Selecciona tu rol:", ["Cliente / Usuario (Solo Lectura)", "Administrador"])

clave_admin = "1234"
es_admin = False

if rol == "Administrador":
    password = st.sidebar.text_input("Contraseña de Administrador", type="password")
    if password == clave_admin:
        es_admin = True
        st.sidebar.success("Modo Administrador Activo")
    elif password != "":
        st.sidebar.error("Contraseña incorrecta")

# ---------------------------------------------------------
# VISTA PRINCIPAL
# ---------------------------------------------------------
st.title("🧮 Reporte de IVA y Análisis de Clientes / Proveedores")

df_ventas = obtener_datos("ventas")
df_compras = obtener_datos("compras")

periodos_v = df_ventas['periodo'].dropna().unique().tolist() if not df_ventas.empty and 'periodo' in df_ventas.columns else []
periodos_c = df_compras['periodo'].dropna().unique().tolist() if not df_compras.empty and 'periodo' in df_compras.columns else []

periodos_disponibles = sorted(list(set(periodos_v + periodos_c)), reverse=True)

st.sidebar.markdown("---")
st.sidebar.header("📅 Filtro de Período")

if periodos_disponibles:
    opcion_periodo = st.sidebar.selectbox("Selecciona el Mes:", ["Todos los Períodos"] + periodos_disponibles)
else:
    opcion_periodo = "Todos los Períodos"
    st.sidebar.info("No hay datos cargados aún.")

# Filtrado por período
if opcion_periodo != "Todos los Períodos":
    if not df_ventas.empty and 'periodo' in df_ventas.columns:
        df_ventas = df_ventas[df_ventas['periodo'] == opcion_periodo]
    if not df_compras.empty and 'periodo' in df_compras.columns:
        df_compras = df_compras[df_compras['periodo'] == opcion_periodo]

# Pestañas principales de visualización
tab_resumen, tab_analisis = st.tabs(["📋 Preliminar de IVA", "📊 Análisis de Clientes y Proveedores"])

with tab_resumen:
    debito_fiscal = df_ventas["iva"].sum() if not df_ventas.empty else 0.0
    credito_fiscal = df_compras["iva"].sum() if not df_compras.empty else 0.0
    neto_ventas = df_ventas["neto"].sum() if not df_ventas.empty else 0.0
    neto_compras = df_compras["neto"].sum() if not df_compras.empty else 0.0
    saldo_iva = debito_fiscal - credito_fiscal

    st.subheader(f"📌 Posición de IVA: **{opcion_periodo}**")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Ventas (Neto)", f"${neto_ventas:,.2f}")
    m2.metric("Total Compras (Neto)", f"${neto_compras:,.2f}")
    m3.metric("Débito Fiscal (Ventas)", f"${debito_fiscal:,.2f}")
    m4.metric("Crédito Fiscal (Compras)", f"${credito_fiscal:,.2f}")

    st.markdown("---")

    if saldo_iva > 0:
        st.error(f"⚠️ **Posición Final:** IVA a Pagar: **${saldo_iva:,.2f}**")
    elif saldo_iva < 0:
        st.success(f"✅ **Posición Final:** Saldo a Favor del Contribuyente: **${abs(saldo_iva):,.2f}**")
    else:
        st.info("ℹ️ **Posición Final:** Saldo Neutro ($0.00)")

    col_v, col_c = st.columns(2)

    with col_v:
        st.subheader("📋 Detalle de Ventas")
        if not df_ventas.empty:
            cols_mostrar = [c for c in ['fecha', 'comprobante', 'nombre', 'cuit', 'neto', 'iva'] if c in df_ventas.columns]
            st.dataframe(df_ventas[cols_mostrar], use_container_width=True)
        else:
            st.info("No hay ventas para este período.")

    with col_c:
        st.subheader("📋 Detalle de Compras")
        if not df_compras.empty:
            cols_mostrar = [c for c in ['fecha', 'comprobante', 'nombre', 'cuit', 'neto', 'iva'] if c in df_compras.columns]
            st.dataframe(df_compras[cols_mostrar], use_container_width=True)
        else:
            st.info("No hay compras para este período.")

with tab_analisis:
    st.subheader(f"📈 Análisis Comercial y Concentración ({opcion_periodo})")

    col_a1, col_a2 = st.columns(2)

    with col_a1:
        st.markdown("### 🏆 Top Clientes (Mayor Facturación Neto)")
        if not df_ventas.empty and 'nombre' in df_ventas.columns:
            top_clientes = df_ventas.groupby('nombre')[['neto', 'iva']].sum().sort_values(by='neto', ascending=False).reset_index()
            
            # Gráfico de Barras
            st.bar_chart(top_clientes.head(10).set_index('nombre')['neto'])
            
            st.markdown("#### Tabla Global de Clientes:")
            st.dataframe(top_clientes, use_container_width=True)
            
            # Filtro por cliente específico
            cliente_sel = st.selectbox("Filtrar historial de un Cliente:", ["Todos"] + top_clientes['nombre'].tolist())
            if cliente_sel != "Todos":
                st.dataframe(df_ventas[df_ventas['nombre'] == cliente_sel][['fecha', 'comprobante', 'neto', 'iva']], use_container_width=True)
        else:
            st.info("No hay datos de ventas disponibles para analizar clientes.")

    with col_a2:
        st.markdown("### 🏬 Top Proveedores (Mayor Compra Neto)")
        if not df_compras.empty and 'nombre' in df_compras.columns:
            top_prov = df_compras.groupby('nombre')[['neto', 'iva']].sum().sort_values(by='neto', ascending=False).reset_index()
            
            # Gráfico de Barras
            st.bar_chart(top_prov.head(10).set_index('nombre')['neto'])
            
            st.markdown("#### Tabla Global de Proveedores:")
            st.dataframe(top_prov, use_container_width=True)

            # Filtro por proveedor específico
            prov_sel = st.selectbox("Filtrar historial de un Proveedor:", ["Todos"] + top_prov['nombre'].tolist())
            if prov_sel != "Todos":
                st.dataframe(df_compras[df_compras['nombre'] == prov_sel][['fecha', 'comprobante', 'neto', 'iva']], use_container_width=True)
        else:
            st.info("No hay datos de compras disponibles para analizar proveedores.")

# ---------------------------------------------------------
# PANEL DE ADMINISTRACIÓN
# ---------------------------------------------------------
if es_admin:
    st.markdown("---")
    st.header("⚙️ Panel de Administración")

    tab_masiva, tab_manual, tab_mantenimiento = st.tabs(["📂 Carga Masiva (AFIP / Excel / PDF)", "✍️ Carga Manual", "🧹 Mantenimiento"])

    with tab_masiva:
        st.markdown("### Subir Archivo de AFIP / Mis Comprobantes")
        
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            tipo_destino = st.selectbox("Destino de los datos:", ["ventas", "compras"])
        with col_m2:
            alicuota_def = st.number_input("Alícuota por defecto si no existe columna IVA (%)", value=21.0, step=0.5)

        archivo_subido = st.file_uploader("Sube el archivo CSV de AFIP, Excel o PDF", type=["csv", "xlsx", "xls", "pdf"])

        if archivo_subido is not None:
            st.info("Procesando estructura de AFIP con datos de Clientes/Proveedores...")
            df_procesado = procesar_archivo_afip(archivo_subido, alicuota_def)

            if df_procesado is not None and not df_procesado.empty:
                st.markdown("#### Vista previa de los datos procesados:")
                st.dataframe(df_procesado, use_container_width=True)

                df_nc = df_procesado[df_procesado['comprobante'].astype(str).str.startswith("NC")]
                cant_nc = len(df_nc)
                neto_nc = abs(df_nc['neto'].sum()) if cant_nc > 0 else 0.0
                iva_nc = abs(df_nc['iva'].sum()) if cant_nc > 0 else 0.0

                neto_total = df_procesado['neto'].sum()
                iva_total = df_procesado['iva'].sum()

                st.markdown("---")
                st.markdown("#### 🔍 Desglose de Totales Detectados:")
                
                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    st.metric("Neto Total (Restadas NC)", f"${neto_total:,.2f}")
                    st.metric("IVA Total (Restadas NC)", f"${iva_total:,.2f}")
                
                with col_t2:
                    if cant_nc > 0:
                        st.warning(f"📄 **Notas de Crédito detectadas:** {cant_nc} comprobante(s)")
                        st.write(f"• **Neto Restado por NC:** `${neto_nc:,.2f}`")
                        st.write(f"• **IVA Restado por NC:** `${iva_nc:,.2f}`")
                    else:
                        st.info("ℹ️ No se detectaron Notas de Crédito en este archivo.")

                st.markdown("---")
                if st.button("Confirmar e Importar a Base de Datos"):
                    guardar_dataframe(tipo_destino, df_procesado)
                    st.success(f"¡Se han importado {len(df_procesado)} registros en {tipo_destino.upper()} correctamente!")
                    st.rerun()
            else:
                st.error("No se pudieron extraer datos válidos del archivo. Revisa el contenido.")

    with tab_manual:
        st.markdown("### Cargar Registro Individual")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            t_mov = st.selectbox("Tipo de Movimiento", ["ventas", "compras"], key="man_tipo")
            f_in = st.date_input("Fecha", key="man_fecha")
            c_in = st.text_input("Número de Comprobante", key="man_comp")
            nom_in = st.text_input("Nombre / Razón Social", key="man_nom")
            cuit_in = st.text_input("CUIT", key="man_cuit")
        with col_f2:
            n_in = st.number_input("Monto Neto ($)", min_value=0.0, step=100.0, key="man_neto")
            ali = st.selectbox("Alícuota IVA", [21.0, 10.5, 27.0, 0.0], key="man_ali")
            iva_calc = n_in * (ali / 100)
            st.write(f"IVA: **${iva_calc:,.2f}**")

        if st.button("Guardar Comprobante"):
            if n_in > 0:
                per_calc = extraer_periodo(str(f_in))
                df_ind = pd.DataFrame([{
                    "fecha": str(f_in),
                    "periodo": per_calc,
                    "comprobante": c_in if c_in else "Manual",
                    "nombre": nom_in if nom_in else "S.D.",
                    "cuit": cuit_in if cuit_in else "S.D.",
                    "neto": n_in,
                    "iva": iva_calc
                }])
                guardar_dataframe(t_mov, df_ind)
                st.success("Guardado.")
                st.rerun()

    with tab_mantenimiento:
        st.markdown("### Limpieza de Base de Datos")
        col_del1, col_del2 = st.columns(2)
        with col_del1:
            if st.button("Vaciar tabla de Ventas", type="secondary"):
                limpiar_tabla("ventas")
                st.warning("Ventas vaciadas.")
                st.rerun()
        with col_del2:
            if st.button("Vaciar tabla de Compras", type="secondary"):
                limpiar_tabla("compras")
                st.warning("Compras vaciadas.")
                st.rerun()