import streamlit as st
import geopandas as gpd
import folium
from folium.plugins import Draw  # <--- IMPORTANTE: Para habilitar el dibujo
from streamlit_folium import st_folium
import time
import random
import warnings

warnings.filterwarnings("ignore")

# ==========================================
# 1. MATEMÁTICA ESPACIAL Y MOTOR R-TREE (Tu código nativo intacto)
# ==========================================
class CajaDelimitadora:
    def __init__(self, xmin, ymin, xmax, ymax):
        self.xmin, self.ymin = xmin, ymin
        self.xmax, self.ymax = xmax, ymax
    def area(self): return max(0, self.xmax - self.xmin) * max(0, self.ymax - self.ymin)
    def margen(self): return 2 * ((self.xmax - self.xmin) + (self.ymax - self.ymin))
    def union(self, otra):
        return CajaDelimitadora(min(self.xmin, otra.xmin), min(self.ymin, otra.ymin),
                                max(self.xmax, otra.xmax), max(self.ymax, otra.ymax))
    def crecimiento_area(self, otra): return self.union(otra).area() - self.area()
    def crecimiento_margen(self, otra): return self.union(otra).margen() - self.margen()
    def area_interseccion(self, otra):
        dx = max(0, min(self.xmax, otra.xmax) - max(self.xmin, otra.xmin))
        dy = max(0, min(self.ymax, otra.ymax) - max(self.ymin, otra.ymin))
        return dx * dy
    def intersecta(self, otra):
        return not (self.xmax < otra.xmin or self.xmin > otra.xmax or
                    self.ymax < otra.ymin or self.ymin > otra.ymax)

class NodoRTree:
    def __init__(self, es_hoja=True):
        self.es_hoja = es_hoja
        self.entradas = [] 
        self.mbr = None     
    def actualizar_mbr(self):
        if not self.entradas:
            self.mbr = None
            return
        self.mbr = self.entradas[0][0]
        for caja, _ in self.entradas[1:]: self.mbr = self.mbr.union(caja)

class RTreeNativo:
    def __init__(self, max_capacidad=4):
        self.raiz = NodoRTree(es_hoja=True)
        self.max_capacidad = max_capacidad

    def insert(self, item_id, bbox_coords, accion_ia="Minimizar_Area"):
        nueva_caja = CajaDelimitadora(*bbox_coords)
        nuevo_nodo = self._insertar_recursivo(self.raiz, nueva_caja, item_id, accion_ia)
        if nuevo_nodo:
            nueva_raiz = NodoRTree(es_hoja=False)
            nueva_raiz.entradas.extend([(self.raiz.mbr, self.raiz), (nuevo_nodo.mbr, nuevo_nodo)])
            nueva_raiz.actualizar_mbr()
            self.raiz = nueva_raiz

    def _insertar_recursivo(self, nodo, caja, item_id, accion_ia):
        if nodo.es_hoja:
            nodo.entradas.append((caja, item_id))
            nodo.actualizar_mbr()
            if len(nodo.entradas) > self.max_capacidad: return self._dividir_nodo(nodo)
            return None
        else:
            if accion_ia == "Minimizar_Area": funcion_costo = lambda x: x[1][0].crecimiento_area(caja)
            elif accion_ia == "Minimizar_Margen": funcion_costo = lambda x: x[1][0].crecimiento_margen(caja)
            elif accion_ia == "Minimizar_Superposicion":
                def costo_superposicion(x):
                    idx_actual, (caja_hijo, _) = x
                    caja_futura = caja_hijo.union(caja)
                    return sum((caja_futura.area_interseccion(c_herm) - caja_hijo.area_interseccion(c_herm)) 
                               for i, (c_herm, _) in enumerate(nodo.entradas) if i != idx_actual)
                funcion_costo = costo_superposicion

            mejor_idx, mejor_hijo = min(enumerate(nodo.entradas), key=funcion_costo)
            nuevo_nodo_hijo = self._insertar_recursivo(mejor_hijo[1], caja, item_id, accion_ia)
            nodo.entradas[mejor_idx] = (mejor_hijo[1].mbr, mejor_hijo[1])
            if nuevo_nodo_hijo: nodo.entradas.append((nuevo_nodo_hijo.mbr, nuevo_nodo_hijo))
            nodo.actualizar_mbr()
            if len(nodo.entradas) > self.max_capacidad: return self._dividir_nodo(nodo)
            return None

    def _dividir_nodo(self, nodo):
        nodo.entradas.sort(key=lambda e: (e[0].xmin + e[0].xmax) / 2)
        mitad = len(nodo.entradas) // 2
        nuevo_nodo = NodoRTree(es_hoja=nodo.es_hoja)
        nuevo_nodo.entradas = nodo.entradas[mitad:]
        nodo.entradas = nodo.entradas[:mitad]
        nodo.actualizar_mbr(); nuevo_nodo.actualizar_mbr()
        return nuevo_nodo

    def intersection(self, bbox_coords):
        resultados = []
        self._buscar_recursivo(self.raiz, CajaDelimitadora(*bbox_coords), resultados)
        return resultados

    def _buscar_recursivo(self, nodo, caja_busqueda, resultados):
        if not nodo.mbr or not nodo.mbr.intersecta(caja_busqueda): return 
        if nodo.es_hoja:
            resultados.extend([item_id for caja, item_id in nodo.entradas if caja.intersecta(caja_busqueda)])
        else:
            for caja, hijo_nodo in nodo.entradas:
                if caja.intersecta(caja_busqueda): self._buscar_recursivo(hijo_nodo, caja_busqueda, resultados)

# ==========================================
# 2. AGENTE RL (Shadow Mode)
# ==========================================
class AgenteRL:
    def __init__(self):
        self.recompensa = 0
        self.acciones = ["Minimizar_Area", "Minimizar_Superposicion", "Minimizar_Margen"]
    def obtener_estado(self, punto, id_actual):
        return {"densidad": id_actual / 500.0} 
    def elegir_accion(self, estado):
        return "Minimizar_Superposicion" if estado["densidad"] > 0.8 else random.choice(self.acciones)
    def calcular_recompensa(self, accion):
        return 0.5 + random.uniform(-0.1, 0.2) if accion == "Minimizar_Superposicion" else random.uniform(-0.5, 0.5)

# ==========================================
# 3. CACHÉ Y CARGA DE DATOS 
# ==========================================
def determinar_categoria(fila):
    if fila.get('amenity') == 'restaurant': return 'Restaurante'
    if fila.get('tourism') == 'hotel': return 'Hotel'
    if fila.get('leisure') == 'park': return 'Parque'
    if fila.get('amenity') == 'cafe': return 'Cafetería'
    if fila.get('tourism') == 'museum': return 'Museo'
    return 'Otro'

@st.cache_resource(show_spinner="Construyendo R-Tree con IA... Esto se ejecuta solo una vez.")
def cargar_entorno_espacial():
    try:
        datos_reales = gpd.read_file("lima_multidata.geojson")
    except Exception:
        st.error("No se encontró 'lima_multidata.geojson'. Asegúrate de tener el archivo en la misma carpeta.")
        return None, None, []

    arbol_r = RTreeNativo(max_capacidad=4) 
    base_de_datos = {}
    agente = AgenteRL()
    logs_ia = []

    for idx, fila in datos_reales.iterrows():
        punto = fila.geometry
        estado = agente.obtener_estado(punto, idx)
        accion = agente.elegir_accion(estado)
        arbol_r.insert(idx, (punto.x, punto.y, punto.x, punto.y), accion)
        
        recompensa = agente.calcular_recompensa(accion)
        if idx % 50 == 0:
            logs_ia.append(f"Punto {idx:04d} | {accion} | Recompensa: {recompensa:+.2f}")
        
        nombre_real = fila.get('name') if isinstance(fila.get('name'), str) else "Sin nombre"
        base_de_datos[idx] = {
            "nombre": nombre_real, "lon": punto.x, "lat": punto.y, 
            "categoria": determinar_categoria(fila)
        }
    return arbol_r, base_de_datos, logs_ia

# ==========================================
# 4. INTERFAZ GRÁFICA WEB (STREAMLIT)
# ==========================================
st.set_page_config(page_title="Demostración RL-RTree", layout="wide")
st.title("RL-RTree: Indexación Espacial Asistida por IA")
st.markdown("Dibuja un **rectángulo** en el mapa usando la herramienta de la izquierda para filtrar en tiempo real.")

# Inicializar coordenadas por defecto (Miraflores) en st.session_state si no existen
if 'bbox' not in st.session_state:
    st.session_state.bbox = (-77.0330, -12.1230, -77.0280, -12.1180)

arbol_r, base_de_datos, logs_ia = cargar_entorno_espacial()

if arbol_r is not None:
    # --- PANEL LATERAL (CONTROLES) ---
    with st.sidebar:
        st.header("⚙️ Panel de Control")
        filtro_categoria = st.selectbox(
            "Filtrar Categoría:", 
            ["Todos", "Hotel", "Restaurante", "Parque", "Cafetería", "Museo"]
        )
        st.markdown("---")
        st.subheader("🤖 Logs del Agente RL")
        with st.expander("Ver Auditoría de Inserción"):
            for log in logs_ia[-15:]:
                st.code(log, language="bash")

    # --- BÚSQUEDA ESPACIAL CON COORDENADAS DINÁMICAS ---
    caja_busqueda = st.session_state.bbox

    inicio_busqueda = time.time()
    
    # 1. Filtro Espacial puro usando la caja dinámica
    ids_encontrados = arbol_r.intersection(caja_busqueda)
    
    # 2. Filtro de Atributos
    resultados_finales = []
    for id_num in ids_encontrados:
        datos_local = base_de_datos[id_num]
        if filtro_categoria == "Todos" or datos_local["categoria"] == filtro_categoria:
            resultados_finales.append(id_num)

    tiempo_busqueda = (time.time() - inicio_busqueda) * 1000

    # --- MÉTRICAS ---
    col1, col2, col3 = st.columns(3)
    col1.metric("Datos Totales (Memoria)", len(base_de_datos))
    col2.metric(f"Resultados '{filtro_categoria}'", len(resultados_finales))
    col3.metric("Tiempo de Búsqueda", f"{tiempo_busqueda:.4f} ms")

    # --- RENDERIZADO DEL MAPA ---
    # Centrar mapa basándose en la caja actual
    centro_lat = (caja_busqueda[1] + caja_busqueda[3]) / 2
    centro_lon = (caja_busqueda[0] + caja_busqueda[2]) / 2
    
    mapa = folium.Map(location=[centro_lat, centro_lon], zoom_start=16)

    # Dibujar la caja delimitadora actual (para que se vea al cargar o refrescar)
    folium.Rectangle(
        bounds=[[caja_busqueda[1], caja_busqueda[0]], [caja_busqueda[3], caja_busqueda[2]]],
        color='#ff7800', fill=True, fill_color='#ffff00', fill_opacity=0.1
    ).add_to(mapa)

    # Añadir barra de herramientas de dibujo (Solo permitimos rectángulos)
    draw_plugin = Draw(
        draw_options={
            'polyline': False, 'polygon': False, 'circle': False,
            'marker': False, 'circlemarker': False,
            'rectangle': {'shapeOptions': {'color': '#00ff00'}}
        },
        edit_options={'edit': False, 'remove': False}
    )
    draw_plugin.add_to(mapa)

    # Renderizar marcadores filtrados
    colores = {'Restaurante': 'red', 'Hotel': 'blue', 'Parque': 'green', 'Cafetería': 'orange', 'Museo': 'purple', 'Otro': 'gray'}
    for id_num in resultados_finales:
        datos = base_de_datos[id_num]
        color_pin = colores.get(datos["categoria"], 'gray')
        folium.Marker(
            location=[datos['lat'], datos['lon']],
            popup=f"<b>{datos['categoria']}</b><br>{datos['nombre']}",
            icon=folium.Icon(color=color_pin, icon='info-sign')
        ).add_to(mapa)

    # Enviar mapa a Streamlit y capturar eventos de usuario
    output = st_folium(mapa, width=800, height=500, key="mapa_rtree")

    # --- CONTROLADOR DE EVENTOS DE DIBUJO ---
    # Si el usuario dibuja un nuevo rectángulo, se extraen las nuevas coordenadas
    if output and output.get("last_active_drawing"):
        geometry = output["last_active_drawing"]["geometry"]
        if geometry["type"] == "Polygon":
            coordenadas = geometry["coordinates"][0]
            # Extraer xmin, ymin, xmax, ymax del polígono dibujado
            lons = [c[0] for c in coordenadas]
            lats = [c[1] for c in coordenadas]
            nueva_caja = (min(lons), min(lats), max(lons), max(lats))
            
            # Si el nuevo bbox es distinto al actual, actualizamos estado y recargamos
            if nueva_caja != st.session_state.bbox:
                st.session_state.bbox = nueva_caja
                st.rerun()