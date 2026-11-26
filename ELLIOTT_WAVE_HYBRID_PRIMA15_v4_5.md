# PRIMA-15 v4.5.0 — Elliott Wave + números primos: investigación y método implementado

## Propósito

Esta nota separa tres cosas que no deben confundirse:

1. **La teoría clásica de Elliott:** marco de interpretación de estructuras impulsivas y correctivas, con reglas, guías y grados anidados.
2. **La aproximación computacional de PRIMA-15:** conteo heurístico reproducible a partir de pivotes ZigZag confirmados y reglas estructurales.
3. **La hipótesis propia de PRIMA-15:** comprobar si la duración de los tramos Elliott coincide con números primos y si esa confluencia mejora la evaluación fuera de muestra.

Una coincidencia entre una onda y una duración prima es una característica estadística que se puede medir; no existe una regla clásica de Elliott que exija que las ondas duren números primos, ni hay base para afirmar que los primos causen los giros.

## 1. Fundamentos de Elliott utilizados

### 1.1 Estructura impulsiva

Una onda motriz en dirección de la tendencia principal se representa en cinco tramos: 1, 2, 3, 4 y 5. En la descripción clásica, los tramos 1, 3 y 5 avanzan con la tendencia y 2 y 4 corrigen. La estructura interna ideal de un impulso es 5-3-5-3-5: las ondas motrices se subdividen en cinco y las correctivas en tres.

Para un **impulso estándar**, el motor comprueba las tres reglas duras que se pueden verificar con los pivotes externos:

- La onda 2 no puede retroceder más allá del origen de la onda 1.
- La onda 3 debe sobrepasar el extremo de la onda 1 y no puede ser la más corta entre 1, 3 y 5.
- La onda 4 no debe solaparse con el territorio de precio de la onda 1.

El conteo se invalida como impulso estándar cuando incumple una regla. Cumplir las reglas no demuestra que el mercado vaya a continuar ni sustituye la comprobación de subondas de menor grado. Algunas formas diagonales tienen reglas propias y no deben confundirse con un impulso estándar.

### 1.2 Correcciones

Las correcciones son más variadas y menos sencillas de identificar que los impulsos. Las familias principales que se contemplan en la teoría son:

- **Zigzag:** estructura A-B-C con subdivisión teórica 5-3-5.
- **Plano (flat):** A-B-C, típicamente 3-3-5.
- **Triángulo:** A-B-C-D-E, típicamente 3-3-3-3-3; puede ser contractivo, expansivo o barrera.
- **Combinaciones:** dos o tres estructuras correctivas conectadas, por ejemplo W-X-Y o W-X-Y-X-Z.
- **Diagonal:** estructura motriz en forma de cuña con solapamiento característico; no es un impulso estándar. La subdivisión interna debe revisarse por grado inferior.

La versión actual busca candidatos ABC exteriores, triángulos contractivos y cuñas diagonales posibles. **No afirma que la subdivisión interna esté confirmada** si solo hay pivotes de grado exterior. Las etiquetas `ABC_ZIGZAG_LIKE` y `ABC_FLAT_LIKE` describen morfología observada, no una clasificación Elliott completa.

### 1.3 Reglas frente a guías

PRIMA-15 separa las reglas estructurales de las guías:

- Las reglas anteriores pueden invalidar un impulso estándar.
- Las relaciones Fibonacci (por ejemplo, retrocesos del 38,2%, 50%, 61,8%, 78,6% y extensiones como 1,618) se usan como referencias de ajuste, no como condiciones obligatorias.
- La alternancia entre ondas 2 y 4 es una guía. El motor actual compara sus profundidades porcentuales, pero no certifica que una corrección sea “sharp” y la otra “sideways”: para eso hace falta clasificar la forma interna en un grado inferior.
- La personalidad de onda (impulso, volumen, ritmo, agotamiento) puede servir de contexto, pero no se convierte en una regla dura de validación.

### 1.4 Grados y fractalidad

La misma organización puede observarse en escalas anidadas. PRIMA-15 calcula cuatro grados con funciones diferentes:

| Grado | Fuente | Papel |
|---|---|---|
| 1m | Hasta 3.000 velas 1m (~2,1 días) | Microestructura y giros cortos |
| 15m | Hasta 5.000 velas 15m (~52,1 días) | Grado operativo principal |
| 1h | 1.250 velas agregadas desde 15m | Contexto intermedio |
| 4h | 312 grupos completos agregados desde 15m | Contexto mayor |

Las velas 1h se forman con grupos completos de cuatro velas 15m. Las 4h requieren dieciséis velas 15m completas. Si hay huecos, desalineación o grupos incompletos, se descartan; no se rellenan con precios inventados. Con 5.000 velas 15m quedan 8 velas sueltas que no completan otro bloque de 4h.

### 1.5 Fibonacci como referencia

En un impulso válido se muestran, entre otras, estas relaciones:

- Retroceso de onda 2 frente a onda 1.
- Extensión de onda 3 frente a onda 1.
- Retroceso de onda 4 frente a onda 3.
- Longitud de onda 5 frente a onda 1.

El motor calcula la distancia relativa a ratios de referencia. El resultado es una puntuación de ajuste de guía; nunca transforma la regla estructural en una simple puntuación Fibonacci. Una buena coincidencia Fibonacci no salva un impulso que incumple una regla dura.

## 2. Método computacional de PRIMA-15

### Paso A — preparar velas cerradas

El contador recibe datos OHLCV cerrados. La vela abierta no puede convertirse en un pivote confirmado. Se utiliza el histórico disponible y se declara su tamaño real; si el proveedor no entrega 5.000, la aplicación no finge haberlo recibido.

### Paso B — obtener pivotes con ZigZag adaptativo

Se calcula el True Range y una mediana ATR porcentual reciente. Para cada grado, el umbral de reversión es una combinación del suelo porcentual del grado y un múltiplo de esa mediana. El algoritmo alterna máximos y mínimos; un extremo solo queda confirmado cuando una vela posterior revierte el umbral.

El objeto de pivote conserva dos tiempos distintos:

- `time`: cuándo ocurrió el extremo.
- `confirmed_at`: cuándo hubo información suficiente para confirmarlo.

**No se permite retrofechar una señal.** En una simulación histórica, una decisión basada en ese pivote solo puede existir a partir de `confirmed_at`, nunca en `time`. El extremo más reciente se expone como provisional, no como hecho confirmado.

### Paso C — contar candidatos

Sobre los pivotes alternos:

- Busca secuencias de seis extremos para comprobar un impulso estándar de cinco tramos.
- Busca secuencias parciales 1-2-3 o 1-2-3-4 y las marca provisionales.
- Busca A-B-C exteriores y compara geometría de B y longitud de C/A como heurística descriptiva.
- Busca cinco swings convergentes para un posible triángulo contractivo.
- Busca una cuña con solapamiento para una posible diagonal, sin tratarla como continuación automática.
- Selecciona el candidato reciente más pertinente, conserva alternativas relevantes y devuelve `NO_CLEAR_ELLIOTT_COUNT` si no hay estructura defendible.

### Paso D — registrar reglas, razones y escenarios

Cada conteo informa el patrón candidato, dirección, rol direccional, score descriptivo, provisionalidad, pivotes, patas, reglas duras, violaciones, ratios, precio de invalidación, siguiente hipótesis y explicación. El score no es una probabilidad estadística.

### Paso E — relacionar cada tramo con números primos

Para cada pata Elliott el motor calcula:

- Dirección y cambio porcentual.
- Amplitud porcentual absoluta.
- Duración en velas del grado y minutos equivalentes.
- Número primo más cercano a la duración.
- Diferencia absoluta entre duración y primo.
- `prime_match` cuando la diferencia es como máximo una vela.

El `prime_time_overlay` expresa qué proporción de las patas cumple ese criterio. No se altera el conteo Elliott para forzar una duración prima.

### Paso F — sintetizar acuerdo o conflicto

En 15m se comparan por separado:

1. Dirección del conteo Elliott (solo si tiene señal direccional válida o provisional explícita).
2. Dirección propia del motor de primos.
3. Ajuste temporal prima de las patas Elliott.

Estados relevantes:

- `ELLIOTT_IMPULSE_AND_PRIME_CONFLUENCE`: impulso estándar completo y válido, dirección coincidente y ajuste temporal de al menos 60% de patas.
- `ELLIOTT_PROVISIONAL_AND_PRIME_CONFLUENCE`: acuerdo con una estructura provisional; no equivale a impulso completo.
- `DIRECTIONAL_CONFLICT`: Elliott y motor de primos apuntan en sentidos opuestos.
- `ELLIOTT_PRIME_TIME_FIT_ONLY`: ajuste temporal sin señal direccional prima confirmada.
- `ELLIOTT_COUNT_WITHOUT_STRONG_PRIME_FIT`: Elliott tiene dirección pero no ajuste primo dominante.
- `NO_VALID_PRIMARY_ELLIOTT_COUNT`: no hay conteo direccional suficiente.

Los triángulos no generan una dirección antes de una ruptura confirmada. Una posible diagonal se trata como riesgo de agotamiento/reversión, no como continuación automática. Una corrección ABC no se toma como confirmación del sesgo principal mientras no esté terminada y su papel esté justificado.

## 3. Qué reciben los LLM de OpenRouter

Perplexity Decider y Nemotron reciben el mismo snapshot del mercado y simulación, con instrucciones de rol distintas. El snapshot incluye:

- Conteos 1m, 15m, 1h y 4h; pivotes confirmados, extremo provisional, patrón, reglas, ratios, patas, invalidación y advertencias.
- Relación de cada pata con la duración prima, dirección del motor de primos y síntesis de confluencia/conflicto.
- Las últimas 350 velas 15m crudas usadas por el motor técnico y hasta 120 velas 1m recientes.
- Las tres dimensiones 3D originales, diagnóstico de mechas, datos de entrada rápida, BTC/ETH, estado de wallet, backtest y memoria histórica indicada en el manifiesto.

Las 5.000 velas 15m se procesan localmente para el conteo; no se envían como 5.000 filas crudas en cada solicitud. Se mandan sus resultados por grado y las velas recientes detalladas para mantener el prompt manejable. El manifiesto registra cuántas velas se procesaron, cuántos pivotes se detectaron, qué grados se incluyeron y la huella del snapshot.

Los LLM deben completar dos familias nuevas de evidencia: `elliott` y `elliott_prime`. Pueden decir que no hay conteo claro. No deben inventar ondas para completar el esquema. Su opinión es evidencia de análisis, no una probabilidad calibrada ni una orden autónoma que sobreescriba las reglas operativas deterministas.

## 4. Qué significa —y qué no— la confluencia Elliott + primos

La hipótesis de investigación es que la estructura de swing (Elliott) y la duración de sus tramos (primos) pueden contener información complementaria:

- Elliott intenta describir la forma y el rol del movimiento dentro de una estructura de mayor grado.
- Los primos ofrecen una prueba de duración y de distribución de tiempos, independiente de la etiqueta Elliott.
- Las dimensiones de amplitud y comportamiento añaden magnitud, velocidad y analogías históricas.

La confluencia no implica causalidad. Una duración igual a 13 velas puede ocurrir por azar; una estructura que parece cinco ondas puede cambiar de grado al incluir más datos; distintas configuraciones ZigZag producen distintos pivotes. La utilidad se establece solo si la combinación mejora resultados walk-forward frente a cada componente por separado.

Por eso la validación futura debe comparar, en exactamente los mismos tramos temporales y con costes idénticos:

1. Primos solamente.
2. Elliott solamente.
3. Elliott + primos.
4. Primos + Elliott + 3D + microestructura.

Se deben medir retorno neto, tasa de TP-first, MAE/MFE, tiempo hasta TP, número de operaciones, drawdown y resultados por régimen de volatilidad. La selección del ZigZag y de ratios se hace en una muestra de entrenamiento; se valida después en periodos cronológicos no utilizados para ajustar parámetros. No se deben mezclar al azar filas contiguas de una misma tendencia entre entrenamiento y prueba.

## 5. Historial: 1.000 frente a 5.000 velas de 15m

- 1.000 velas 15m equivalen a unos 10,4 días; aportan contexto limitado para 4h.
- 5.000 velas equivalen a unos 52,1 días; agregan aproximadamente 1.250 velas 1h y 312 velas 4h completas.

Se eligió 5.000 para ampliar la posibilidad de reconocer estructuras de grado mayor. No significa que sean suficientes para analizar tendencias de meses o años. Si el objetivo cambia a ondas semanales/mensuales, habría que descargar también velas diarias/semanales de forma directa, no inferirlas a partir de 52 días.

La aplicación pagina la consulta histórica en lotes de hasta 1.000 velas, las guarda en SQLite y conserva un margen de histórico. Al iniciar, si hay menos velas de las solicitadas, intenta completar el objetivo incluso cuando solo falte una pequeña parte. En `Render` debe comprobarse que la variable `HISTORY_CANDLES` esté en `5000`; una variable configurada manualmente en el Dashboard puede prevalecer sobre el valor del archivo `render.yaml`.

## 6. Limitaciones que deben permanecer visibles

- Es un contador heurístico, no un analista Elliott humano certificado.
- Las reglas del impulso son verificables; las subdivisiones internas de correcciones/diagonales/triángulos no se certifican con pivotes exteriores.
- El umbral ATR-ZigZag cambia el número y la ubicación de los pivotes.
- Un pivote se confirma después del extremo; esa latencia debe reflejarse en cualquier backtest.
- El conteo puede ser ambiguo y cambiar con el grado temporal.
- Las coincidencias Fibonacci y primas no son probabilidades de éxito.
- No se afirma que la teoría de Elliott tenga ventaja estadística para RENDER hasta completar las pruebas walk-forward fuera de muestra.

## 7. Referencias de investigación

1. Elliott Wave International — [Impulse](https://www.elliottwave.com/waveopedia/impulse/): estructura impulsiva y reglas principales.
2. Elliott Wave International — [Corrective Waves](https://www.elliottwave.com/waveopedia/corrective-waves/): familias de correcciones y complejidad.
3. Learn — [Corrective Waves](https://silverpipss-organization.gitbook.io/learn/elliott-wave-principle/all-lessons/1.-introduction/1.7-corrective-waves): esquemas 5-3-5, 3-3-5, 3-3-3-3-3 y combinaciones.
4. Elliott Wave International — [Alternation](https://www.elliottwave.com/waveopedia/alternation/): alternancia como guía.
5. Elliott Wave International — [Diagonals](https://www.elliottwave.com/waveopedia/elliott-wave-pattern-diagonals/): patrones diagonales y solapamiento.
6. Elliott Wave International — [Fibonacci Relationships](https://www.elliottwave.com/waveopedia/fibonacci-relationships/): ratios como guías entre ondas.
7. StockCharts ChartSchool — [Guidelines for Applying Elliott Wave Theory](https://chartschool.stockcharts.com/table-of-contents/market-analysis/elliott-wave-analysis-articles/guidelines-for-applying-elliott-wave-theory): carácter típico y uso de guías.
8. Wawer, Chudziak & Niewiadomska-Szynkiewicz (2024), *Large Language Models and the Elliott Wave Principle: A Multi-Agent Deep Learning Approach to Big Data Analysis in Financial Markets*, Applied Sciences 14(24), 11897, [DOI 10.3390/app142411897](https://doi.org/10.3390/app142411897). El artículo explora Elliott + LLM multiagente, pero también comenta subjetividad, dificultad de criterios sistemáticos, sobreajuste y problemas en mercados laterales; no demuestra una ventaja universal ni valida esta implementación de PRIMA-15.

---

**Estado de validación de esta versión:** las pruebas locales automáticas y los datos sintéticos validan reglas, agregación, no-lookahead y formato del snapshot. En este entorno no se ha descargado el histórico real de RENDER ni se ha probado una llamada real a OpenRouter. El rendimiento de Elliott + primos sobre RENDER debe considerarse todavía una hipótesis en evaluación.
