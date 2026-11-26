# PRIMA-15 Adaptive v4.5.0 · Elliott + Prime Hybrid

Monitor ligero Flask para **RENDERUSDT en velas de 15 minutos**. Descarga histórico público, lee el precio reciente, estudia máximos/mínimos y duraciones primas, consulta dos modelos LLM independientes y deja que el comité gestione una cartera de posiciones **simuladas**.

## Qué hace

Versión 4.5.0: incorpora un conteo Elliott explicable en cuatro grados (1m/15m/1h/4h), lo compara con las duraciones primas y aumenta a 5.000 las velas de 15 minutos que se procesan para estructura. Conserva el motor 3D, aprendizaje walk-forward, auditoría LLM y ciclo continuo simulado de v4.4.0.

1. **Precio e histórico:** consulta RENDERUSDT Spot en Binance y usa Bybit Spot como respaldo. No necesita claves de exchange ni permisos de trading. Guarda hasta 5.000 velas de 15 minutos en SQLite (~52,1 días).
2. **Dimensión temporal:** genera dinámicamente ventanas primas de 7 a 233 velas disponibles, mide cuánto dura cada tramo, calcula el primo cercano y compara proporciones entre duraciones. También mide cuántas escalas ubican al cierre cerca de la parte baja o alta de su rango.
3. **Dimensión de amplitud:** mide cada onda en porcentaje, su velocidad porcentual por vela, su amplitud normalizada por ATR, la relación con la última onda del mismo sentido y el retroceso entre ondas opuestas. Identifica expansión o contracción de la presión compradora/vendedora y aproxima sus relaciones a proporciones derivadas de primos.
4. **Dimensión de comportamiento:** construye una huella de 13 velas con retorno, recorrido total, eficiencia, volatilidad, proporción de velas alcistas, mechas, aceleración y cambio de volumen. Busca hasta seis patrones históricos comparables y mide qué pasó 4, 8 y 12 velas después. Los resultados analógicos se calculan solo con datos que ya existían antes de la huella actual; no usa velas futuras.
5. **Síntesis tridimensional:** reúne pistas temporales, de amplitud y de comportamiento. Sus aportes al puntaje LONG/SHORT están limitados a seis puntos por lado para que no suplanten la estructura de precio ni una confirmación de rechazo.
6. **Detector explicable de mechas:** distingue forma geométrica de mecha, posición en el rango de la vela (P15), posición de la mecha en escalas primas, barrido y recuperación de soporte, rechazo de resistencia, volumen, RSI, impulso y divergencia. Indica qué pruebas se cumplieron y qué bloqueadores siguen pendientes. Una vela abierta queda como provisional.
7. **Detección de rebote y extremos:** calcula si el precio está en zona baja y si aparece un giro al alza. `bottom_bounce_confirmed` es un diagnóstico del motor técnico; la decisión del comité usa la jerarquía de continuidad y excepciones descrita abajo. Una mecha solo es rechazo confirmado si la vela está cerrada, la geometría es suficiente, el extremo está en varias escalas, recupera soporte/rechaza resistencia y existe otra confirmación independiente.
8. **Comité OpenRouter:** consulta dos modelos independientes de la lista gratuita del usuario. Principales: `perplexity/pplx-decider-flash-v1.3:free` y `nvidia/nemotron-3.5-lightning:free`. Si alguno falla, ERNIE, Perplexity Decider completo, Nemotron Ultra/Nano, Recursive Flint, Nemotron Super VL, Particle o Liquid pueden completar el puesto ausente.
9. **Jerarquía de decisión:** sin rechazo confirmado, Nemotron 3.5 Lightning valida continuidad en horizontes 3/5/8; ante rechazo inferior/superior confirmado, prevalece Perplexity. Si la tendencia no supera el umbral o la continuidad es incierta, la política direccional configurada puede proponer SHORT. `fast_entry` aporta TP-first, spread, presión y obstáculos como evidencia y advertencias; por defecto NO bloquea una decisión direccional válida del comité. Solo bloquea si `FAST_ENTRY_ENFORCE_GATE=true`. Sin dos respuestas LLM válidas o sin datos actuales de mercado, no se abre ni se invierte.
10. **Ciclo continuo de posiciones simuladas:** mantiene una sola posición a la vez. Si el comité repite el lado actual, la mantiene sin cobrar otra vez. Si cambia el lado, cierra la anterior y abre la contraria. Cuando Take Profit cierra una posición, el worker fuerza una evaluación LLM inmediata con precio/velas actuales y wallet en estado FLAT; si el nuevo comité entrega dos votos válidos y una dirección LONG/SHORT, abre la siguiente posición en ese mismo ciclo. Nunca reutiliza la decisión que abrió la operación cerrada. Si OpenRouter falla, falta quórum o devuelve WAIT, permanece FLAT hasta la siguiente evaluación programada. No transmite órdenes a ningún exchange.
11. **Cartera en porcentaje:** el saldo inicial es un índice de `100.000%`. Toda la cartera, rentabilidad, P&L, retornos, costes y registro de operaciones se presentan en porcentaje; la API de cartera tampoco expone importes monetarios ni campos USD.
12. **Coste de apertura:** al abrir se descuenta `SIM_OPEN_FEE_PCT=0.035` del saldo disponible (0,035% del wallet en ese momento). No se cobra comisión al cerrar. Una reversión cierra sin coste y aplica una nueva comisión únicamente por abrir la posición contraria.
13. **Persistencia y memoria:** wallet, posiciones activas, operaciones cerradas, comisiones y resultados quedan guardados en SQLite. La base de datos anterior se conserva y las tablas nuevas se crean automáticamente.
14. **Conteo Elliott multigrado:** analiza hasta 5.000 velas cerradas de 15m con pivotes ZigZag adaptados al ATR; desde ellas agrega velas completas de 1h y 4h, y combina el grado micro 1m (hasta 3.000 velas). Busca impulsos estándar 1–2–3–4–5, conteos parciales, correcciones ABC, posibles diagonales y triángulos. Las reglas duras invalidan impulsos; Fibonacci y alternancia solo aportan contexto. No fuerza un conteo cuando los extremos no son suficientes o las estructuras compiten.

## Diagnóstico rápido TP-first (v4.4.0, conservado en v4.5.0)

La dirección del comité sigue siendo el permiso direccional de la cartera simulada. El motor TP-first aporta una evaluación independiente de calidad basada en datos de un minuto, microestructura y contexto de mercado. Desde v4.4.0 esta evaluación es orientativa por defecto: un spread amplio, una analogía insuficiente o una ausencia temporal del order book se registra como advertencia, pero no anula un consenso LLM válido. El veto duro se puede reactivar manualmente con `FAST_ENTRY_ENFORCE_GATE=true`.

1. **Velas 1m:** se descargan hasta 3.000 velas de un minuto. Se utilizan hasta 2.400 velas cerradas para construir analogías históricas; las características actuales incluyen retornos de 1/3/5/10m, cruce y separación EMA 9/21, pendiente/aceleración, RSI 7, distancia VWAP, eficiencia de recorrido, rango y relación de volumen.
2. **Etiqueta TP-first:** cada situación histórica se etiqueta como `TP_FIRST`, `ADVERSE_FIRST`, ambas barreras en la misma vela (pérdida conservadora) o sin toque dentro del horizonte. Para la configuración inicial, el objetivo es 0,20%, la barrera adversa histórica es 0,12% y el horizonte es 20 minutos. Esa barrera adversa solo clasifica el histórico; no es un stop activo.
3. **Analogías menos redundantes:** el sistema compara la huella actual con las más parecidas y exige que los ejemplos seleccionados estén separados al menos 20 minutos. Así evita contar muchas velas consecutivas de un único impulso como si fueran observaciones independientes. Usa tasa TP-first suavizada y límite Wilson inferior al 80%; no llama a esos valores probabilidad garantizada.
4. **Microestructura:** cada 20 segundos consulta order book y operaciones recientes de RENDER. Calcula spread, desequilibrio de profundidad y agresión taker, filtrando operaciones individuales de más de 45 segundos para no contaminar el flujo con prints antiguos. Si ambas fuentes están desactualizadas, el spread excede 8 puntos básicos o libro y agresores contradicen simultáneamente la dirección, la oportunidad queda marcada como no elegible y se informa al comité; por defecto no bloquea la posición.
5. **Contexto BTC/ETH:** calcula retornos de 3, 5 y 10 minutos para BTCUSDT y ETHUSDT. Son confirmación externa y no sustituyen a RENDER. También calcula la fuerza relativa de RENDER frente al promedio de BTC/ETH: si BTC y ETH caen, pero RENDER los supera por al menos 0,08 puntos porcentuales en 5m, no se bloquea LONG solo por el movimiento macro; se aplica la lógica simétrica a SHORT si RENDER queda rezagado cuando los líderes suben. Si ambos líderes contradicen y RENDER no muestra desacople suficiente, ese lado queda bloqueado.
6. **Espacio al objetivo:** comprueba máximos/mínimos recientes de 1m y los niveles estructurales de soporte/resistencia del motor de 15m. No abre si el obstáculo más cercano deja menos del 112% del recorrido requerido al TP.
7. **Ventaja direccional:** combina tasa TP-first histórica (68%) y presión viva (32%), con un componente adicional de expansión de volumen y fuerza relativa. Si LONG y SHORT quedan a menos de 4,5 puntos, el diagnóstico marca empate y no recomienda un candidato rápido.
8. **Filtro de entrada inicial:** por defecto exige al menos 25 analogías independientes, tasa TP-first ≥62%, límite Wilson ≥50%, presión viva ≥58/100, spread ≤8 bps y tiempo mediano histórico hasta TP ≤15 minutos. El diagnóstico también calcula el rendimiento neto estimado tras comisión y spread; sus filtros describen la calidad del setup, pero no bloquean el comité salvo que se active el modo estricto.
9. **Comité LLM:** Perplexity y Nemotron reciben las velas de 1m recientes, el resumen fast-entry, order book, flujo taker, BTC/ETH y los demás bloques. El fast-entry es orientativo por defecto; `FAST_ENTRY_ENFORCE_GATE=true` permite volver a activar el veto duro de TP-first.

La comprobación rápida se actualiza con precio cada 8 segundos y datos 1m/microestructura aproximadamente cada 20 segundos. El comité consulta OpenRouter cada cinco minutos, aunque no haya un candidato fast-entry, y vuelve a consultar inmediatamente después del Take Profit. El diagnóstico fast-entry no veta una dirección del comité en modo ADVISORY; solo lo hace si `FAST_ENTRY_ENFORCE_GATE=true`. Solo un comité con dos respuestas válidas y dirección final LONG/SHORT puede abrir o revertir. Una tasa histórica TP-first alta no asegura que la siguiente operación toque el objetivo; debe validarse con operaciones simuladas reales y suficiente muestra.

### Parámetros de la entrada rápida

| Variable | Valor inicial | Función |
|---|---:|---|
| `FAST_MARKET_REFRESH_SECONDS` | `20` | Refresco de velas 1m, libro, agresores y BTC/ETH |
| `FAST_MARKET_HISTORY_CANDLES` | `3000` | Velas 1m conservadas localmente |
| `FAST_ENTRY_REFERENCE_CANDLES` | `2400` | Velas cerradas utilizadas para analogías |
| `FAST_ENTRY_HORIZON_MINUTES` | `20` | Horizonte de etiqueta TP-first |
| `FAST_ENTRY_ADVERSE_PCT` | `0.12` | Barrera adversa de etiquetado, no stop real |
| `FAST_ENTRY_MIN_SAMPLES` | `25` | Analogías temporalmente independientes mínimas |
| `FAST_ENTRY_MIN_HIT_RATE_PCT` | `62` | Tasa TP-first mínima |
| `FAST_ENTRY_MIN_WILSON_PCT` | `50` | Límite inferior Wilson mínimo |
| `FAST_ENTRY_MIN_LIVE_CONFIRMATION_PCT` | `58` | Presión direccional actual mínima |
| `FAST_ENTRY_MAX_SPREAD_BPS` | `8` | Spread máximo aceptado |
| `FAST_ENTRY_MAX_MINUTES_TO_TARGET` | `15` | Mediana máxima histórica hasta TP |
| `FAST_ENTRY_MIN_DIRECTIONAL_EDGE_GAP_PCT` | `4.5` | Diferencia mínima entre calidad LONG y SHORT |

## Regla de decisión y ciclo continuo, paso a paso

- **Continuidad sin anomalía:** Nemotron 3.5 Lightning guía la dirección solo cuando las ventanas 3/5/8 muestran acuerdo superior al 60% y su voto coincide con esa dirección.
- **Excepción:** si el motor confirma una mecha inferior con recuperación de soporte, o una mecha superior con rechazo de resistencia, prevalece la decisión del primer LLM (Perplexity).
- **Incertidumbre válida:** si no se confirma tendencia o los votos no validan la hipótesis, la política simulada resuelve SHORT.
- **Sin quórum técnico:** si faltan dos respuestas LLM válidas, falla la API o los datos no son fiables, el estado es WAIT y se conserva la posición abierta; un fallo de API nunca equivale a SHORT.

La convicción del LLM no es una probabilidad calibrada de ganar. La regla SHORT ante incertidumbre es un sesgo explícito de esta simulación y puede perder dinero virtual durante tendencias alcistas; se debe evaluar con suficiente histórico antes de considerar cualquier uso fuera del entorno simulado.


## Versión automejorada y límites del aprendizaje

Esta versión incorpora aprendizaje walk-forward persistente en `adaptive_learning.py`. El sistema mejora únicamente parámetros explícitos; **no reescribe sus archivos Python, prompts, reglas de cartera ni código**.

1. Cada evaluación LLM válida con dos votos, decisión LONG/SHORT y precio registra en SQLite el instante, precio de referencia, decisión, fundamento, votos, dimensiones y evidencia de tendencia/rechazo.
2. La predicción solo se etiqueta cuando existen velas de 15 minutos completamente cerradas y posteriores a la vela en que se tomó la decisión. Nunca usa máximos/mínimos de la vela de predicción ni velas futuras aún abiertas.
3. Guarda resultados a 1, 2, 4, 8 y 12 velas. El ajuste principal usa el horizonte de 4 velas (una hora) y una ventana máxima de 120 predicciones.
4. Con menos de 30 resultados completos, mantiene pesos neutrales, acuerdo de tendencia del 60% y TP base del 0,20%. No aprende a partir de muestras insuficientes.
5. Con al menos 30 resultados, estima relaciones entre cada dimensión y el retorno futuro; adapta sus pesos solo entre ×0,85 y ×1,15. La contribución conjunta de las tres dimensiones sigue limitada a seis puntos por lado.
6. Ajusta el umbral de continuidad Nemotron dentro del rango 58–72%, según el desempeño histórico de decisiones basadas en continuidad. El requisito es **superar estrictamente** el umbral, no igualarlo.
7. Compara TP de 0,20% y 0,30% mediante el recorrido OHLC posterior observado, suma el coste de apertura del 0,035% y no cobra al cierre. Elige 0,30% solo si supera al 0,20% por más de 0,005 puntos porcentuales de wallet en el promedio; en caso contrario escoge el menor.
8. Mide el historial de cada modelo y rol (acierto direccional y retorno medio). Se muestra como diagnóstico y no altera automáticamente los votos: no se permite que una muestra pequeña haga que el modelo se autoasigne autoridad.
9. Guarda cambios efectivos de perfil en `adaptive_profile_history`. El panel expone muestras, exactitud, retorno medio, precisión de continuidad, comparación TP, pesos y fiabilidad histórica por modelo.

La simulación del Take Profit busca el primer OHLC futuro que toca el objetivo; si no se toca dentro de cuatro velas, cierra el replay al cierre de la cuarta. Es una aproximación histórica, no un backtest tick-a-tick: el orden intravela, deslizamiento, spread y movimientos más rápidos que la frecuencia de consulta pueden cambiar el resultado real. El gestor activo comprueba el precio en cada sondeo de mercado. El TP no es garantía de beneficio.

### Tablas nuevas de SQLite

- `adaptive_predictions`: cada decisión y las variables que vio el comité.
- `adaptive_outcomes`: resultados ya observables a los cinco horizontes.
- `adaptive_profile_history`: versiones de parámetros aplicados.

No hace falta crear una base nueva. Al iniciar, se crean estas tablas si faltan y se recalcula el perfil desde resultados cerrados existentes. Si se cambia de exchange o símbolo, el histórico de mercado y aprendizaje se limpia para no mezclar series; la wallet simulada y las operaciones cerradas se conservan.

## Cómo leer las tres dimensiones

- **Temporal:** para cada ventana prima calcula `P=(cierre-mínimo)/(máximo-mínimo)`. P≤0,23 ubica el cierre en el cuarto inferior aproximado del rango y P≥0,77 en el superior. Además, registra la mecha baja/alta por separado para no confundir dónde cerró la vela con dónde llegó su extremo.
- **Amplitud:** cada onda se expresa como variación porcentual entre pivotes. La contracción de las ondas bajistas es una pista de menor presión vendedora; no equivale por sí sola a un piso. La expansión indica que la onda se está ampliando frente a su precedente comparable.
- **Comportamiento:** la huella de 13 velas se compara con segmentos antiguos separados entre sí. Para cada horizonte se informa dirección, retorno ponderado, proporción de casos positivos, similitud media y número de muestras. La similitud no es una probabilidad de éxito.
- **Mechas:** `P15=(cierre-mínimo_de_vela)/(máximo_de_vela-mínimo_de_vela)` sitúa el cierre dentro de su propia vela. Por separado, `P_baja(n)` y `P_alta(n)` ubican el extremo de la mecha respecto del rango previo de cada ventana prima. El programa también mide `mecha/rango` y `mecha/cuerpo`. Una mecha solo se marca como rechazo confirmado si la vela está cerrada, cumple la geometría mínima, está en extremo de varias escalas, recupera soporte/rechaza resistencia y tiene al menos otra confirmación independiente. Sin recuperación de soporte o rechazo de resistencia se clasifica como mecha provisional/no confirmada, aunque sea larga.

Las coincidencias entre dimensiones son evidencia descriptiva. Para evaluar si aportan ventaja real hay que contrastarlas en pruebas walk-forward y con costes de apertura de la cartera simulada.

## Contabilidad porcentual

La wallet comienza en `100.000%`:

- Apertura: comisión = saldo previo × `0.035 / 100`; la comisión se resta al instante.
- Exposición predeterminada: `100%` del saldo restante.
- LONG: retorno del precio = `(precio_actual / entrada - 1) × 100`.
- SHORT: retorno del precio = `((entrada - precio_actual) / entrada) × 100`; un movimiento de -1% en precio equivale a +1% sobre la exposición corta, antes del coste de apertura.
- Al cerrar, el retorno de precio se aplica al capital de la posición y se suma al saldo; el cierre no descuenta comisión.
- Equity = wallet realizada + P&L flotante de la posición activa.

Ejemplo ilustrativo: al abrir desde `100.000%`, la wallet pasa a `99.965%`; si el lado obtiene +1,000% de variación favorable usando el 100% de la wallet, al cerrar se suma el resultado porcentual correspondiente. La comisión ya estaba cobrada al abrir.

## Ciclo automático posterior al Take Profit (v4.2)

La posición se monitoriza en cada consulta de precio. Al alcanzar el TP, se cierra sin comisión, se registra la operación y se produce una transición a FLAT. Esa transición fuerza una nueva evaluación OpenRouter aunque todavía no haya vencido `OPENROUTER_INTERVAL_SECONDS`. El snapshot nuevo se construye después del cierre, por lo que ambos LLM ven la wallet sin posición y la operación recién cerrada dentro del historial. Si se obtiene un consenso válido LONG/SHORT con dos modelos, se abre la nueva posición usando el precio actual y se descuenta una sola comisión de apertura del 0,035%. Si la respuesta es WAIT, incompleta, error o sin quórum, no se abre: la wallet queda FLAT y se espera al siguiente ciclo programado. La decisión anterior no se reutiliza.

## Conteo Elliott y relación con números primos (v4.5.0)

Consulta también **`ELLIOTT_WAVE_HYBRID_PRIMA15_v4_5.md`** para la investigación teórica, criterios de conteo, límites, relación con primos y referencias documentales.

El contador Elliott es una cuarta capa estructural: no sustituye las tres dimensiones anteriores ni el motor de primos. Las series 1m/15m/1h/4h se analizan por separado para distinguir microestructura, grado operativo, contexto intermedio y contexto mayor.

### Historial y agregación

- **15m:** hasta 5.000 velas cerradas (~52,1 días). Se descargan por páginas compatibles con los límites de los exchanges y se almacenan en SQLite.
- **1m:** hasta 3.000 velas cerradas (~2,1 días), reutilizadas desde el motor fast-entry.
- **1h:** agregación de grupos completos de cuatro velas 15m alineadas.
- **4h:** agregación de grupos completos de dieciséis velas 15m alineadas.

Las agrupaciones incompletas o con huecos se descartan. Si la base existente solo contiene 3.000 velas, al iniciar se intenta completar hasta 5.000; si falla la conexión, el motor muestra el número real disponible y no afirma que procesó el objetivo completo. `HISTORY_CANDLES` puede ajustarse entre 300 y 5.000.

### Cómo se forma el conteo

1. Se calcula un umbral de reversión basado en la mediana del True Range reciente y un multiplicador por grado. Ese umbral evita que cada fluctuación menor se convierta en una onda.
2. El ZigZag alterna máximos y mínimos. Un extremo solo pasa a confirmado cuando una vela posterior revierte el umbral requerido. El extremo más reciente queda separado como provisional y no se utiliza para cerrar un conteo.
3. Con seis pivotes alternos se comprueba un impulso estándar de cinco tramos. Para un impulso alcista, por ejemplo, se revisa que la onda 2 no cruce el origen de la 1, que la 3 supere el extremo de la 1 y no sea la más corta, y que la 4 no entre en el territorio de la 1. Las condiciones se invierten para un impulso bajista.
4. Si no hay cinco ondas completas, el motor puede mostrar un conteo parcial 1–2–3 o 1–2–3–4, pero se etiqueta como provisional. No se presenta como impulso terminado.
5. Se buscan candidatos ABC, triángulos contractivos y diagonales. Son hipótesis provisionales con pivotes exteriores; el programa no afirma que ha verificado las subdivisiones internas 5-3-5, 3-3-5 o 3-3-3-3-3 si no tiene un conteo de menor grado suficiente.
6. Las razones Fibonacci de ondas 2, 3, 4 y 5 se muestran como guías. La alternancia solo informa la diferencia de profundidad entre ondas 2 y 4; no certifica por sí sola la forma sharp/sideways, que requiere estudiar subondas inferiores.

### Cómo se relaciona con los primos

Para cada tramo Elliott se guardan: dirección, cambio porcentual, amplitud, duración en velas, minutos, primo más cercano y diferencia frente a ese primo. Un tramo de 13 velas tiene ajuste exacto a 13; uno de 12 tiene una diferencia de una vela. El porcentaje de tramos con diferencia de hasta una vela produce el `prime_time_overlay` de cada grado.

La síntesis 15m compara tres cosas separadas: dirección del conteo Elliott, dirección de la señal del motor de primos y ajuste temporal de los tramos. El sistema puede concluir confluencia, conflicto direccional, ajuste de tiempo sin dirección o falta de un conteo primario válido. Nunca interpreta la coincidencia como causalidad ni como probabilidad de beneficio.

### Papel de los LLM

Perplexity y Nemotron reciben el mismo snapshot, que incluye las cuatro lecturas de grado, los pivotes, reglas estructurales, fase, proporciones, duración y superposición prima. Cada modelo debe clasificar dos familias adicionales (`elliott` y `elliott_prime`) y explicar los valores que sustentan su lectura. Un conteo ambiguo puede responder `NO_CLEAR_COUNT` o `INSUFFICIENT`; no se exige inventar un patrón para completar la evaluación.

El conteo Elliott no cambia por sí solo las órdenes simuladas. Alimenta a los LLM y al panel como evidencia estructural, que después se puede contrastar con el resultado de precio a 1, 2, 4, 8 y 12 velas.

### Limitaciones y control de sesgo

Elliott admite grados y conteos alternativos; el ZigZag también depende de su umbral. Por eso PRIMA-15 muestra reglas duras, candidatos provisionales y falta de evidencia por separado. Los pivotes se confirman con retraso: un extremo ocurrió antes de que fuera confirmable. Las velas cerradas evitan usar el rango incompleto actual, pero una señal basada en un pivote confirmado no debe retrofecharse como si hubiera estado disponible en el instante del máximo/mínimo.

El historial más largo mejora el contexto y permite contar grados mayores; no convierte el conteo en una predicción cierta. La utilidad se medirá con pruebas walk-forward, comparando Elliott solo, primos solo y la combinación, con costes de apertura, spread y resultados fuera de muestra.

## Auditoría del contexto enviado a los LLM (v4.1)

En cada ciclo se construye **un único snapshot JSON** y se entrega completo a cada modelo que sea consultado. Los modelos reciben el mismo contenido de mercado/simulación; lo que cambia es la instrucción de rol (Perplexity: excepción/rechazo; Nemotron: continuidad). Cada voto queda asociado a una huella SHA-256 del snapshot común, al tamaño en bytes y a la cobertura de sus evaluaciones.

El snapshot incluye:

1. **Identificación y mercado:** símbolo, intervalo, precio observado, hora del snapshot, proveedor y estadísticas de mercado de 24 horas.
2. **Motor determinista completo:** todas las claves devueltas por `engine.analyze`, puntuaciones LONG/SHORT, señal previa a IA, razones por lado, ATR, RSI, soportes/resistencias, pivotes y diagnóstico completo de mechas.
3. **Dimensión temporal:** todas las escalas primas calculadas y sus posiciones/lecturas; no se reduce a las primeras 18.
4. **Dimensión de amplitud:** todas las piernas de ondas, variaciones porcentuales, duración, velocidad, proporciones y retrocesos.
5. **Dimensión de comportamiento:** huella actual, síntesis, todos los horizontes y todos los casos analógicos resueltos que produzca el motor.
6. **Velas fuente:** hasta las últimas 350 velas de 15 minutos que usa el motor, con OHLCV, volumen cotizado, número de operaciones, cierre y marca de vela cerrada/abierta.
7. **Cartera simulada:** wallet y equity en porcentaje, P&L realizado/no realizado, comisiones, exposición, posición y hasta 20 operaciones cerradas recientes.
8. **Aprendizaje y validación:** resumen de aprendizaje, resumen de backtest, hasta 20 ejemplos etiquetados, hasta 20 señales recientes y hasta 12 predicciones anteriores con resultados ya observados por horizonte.
9. **Perfil adaptativo:** todos los campos calculados del perfil vigente, pesos de dimensiones, umbral de continuidad, selección TP, fiabilidad de modelos/roles, muestras y hasta seis cambios recientes.

### Las 20 familias que cada modelo debe clasificar

`market`, `cross_market`, `raw_candles`, `engine`, `policy`, `temporal`, `prime`, `amplitude`, `behavior`, `wick`, `trend`, `signals`, `backtest`, `prior_memory`, `portfolio`, `learning`, `adaptive_profile`, `fast_entry`, `elliott` y `elliott_prime`.

No basta con que el modelo devuelva LONG/SHORT. También debe emitir una lectura y una base para cada bloque. Si falta una clasificación, está vacía o utiliza una opción inválida, la respuesta se considera incompleta y se intenta un modelo de respaldo; nunca se presenta como cobertura total. El panel muestra la cobertura por modelo y la huella del snapshot.

**Particularidad de Perplexity Decider:** no genera una explicación conversacional tradicional. Se le hacen preguntas tipadas separadas sobre el mismo `state` JSON, y devuelve una opción y su distribución de probabilidades. Esas distribuciones quedan visibles como base estructurada; no se deben confundir con una explicación textual del modelo. Los modelos de chat deben entregar `basis` textual para cada bloque. La documentación de OpenRouter describe Decider como un modelo de decisiones tipadas sobre un estado y admite múltiples preguntas nombradas en una solicitud. <https://openrouter.ai/for-ai/>

### Límites explícitos del envío

No se envían todas las 5.000 velas de 15m una por una: el contador Elliott procesa todo el histórico cerrado y remite sus conteos, pivotes, reglas, duraciones y síntesis por grado; los LLM reciben, además, las últimas 350 velas crudas que utiliza el motor técnico y las últimas 120 de 1m. Así se les entrega el resultado del contexto de 52 días sin llenar la solicitud con 5.000 filas repetidas. Los historiales auxiliares se limitan a 20 operaciones, 20 ejemplos de backtest, 20 señales, 12 predicciones anteriores y seis cambios del perfil adaptativo. El manifiesto informa esos límites y cuenta cuántos registros se enviaron. Tampoco se envían API keys, secretos, logs de servidor ni estado visual de la interfaz. Esos datos no son evidencia de mercado y no deben compartirse con un LLM.

La etiqueta `100% de cobertura` significa que el modelo completó las 20 clasificaciones requeridas sobre el snapshot; **no demuestra que su razonamiento interno haya atendido perfectamente cada dato ni que su lectura sea correcta**. Por eso se conserva la base textual/distribución de cada bloque y se compara con los resultados posteriores.

En una prueba local sintética de 3.000 velas históricas, el snapshot de mercado de la ventana activa ocupó aproximadamente 84 KB en JSON; el tamaño real varía por los pivotes, analogías, wallet e historial. La interfaz registra el tamaño exacto y SHA-256 en cada evaluación.

## OpenRouter y frecuencia

La evaluación normal corre cada cinco minutos por defecto (`OPENROUTER_INTERVAL_SECONDS=300`), tanto cuando la wallet está FLAT como mientras existe una posición. También se ejecuta inmediatamente después de un Take Profit para decidir la siguiente operación con un snapshot actualizado. En una evaluación normal se necesitan dos respuestas principales; se prueban respaldos solo cuando falta un voto. Cada respuesta debe cubrir el 100% de las 20 familias. Para cuidar el plan gratuito, se conserva un máximo inicial de 20 comités por día (normalmente unos 40 llamados a modelo si responden los dos principales; los respaldos pueden elevar el consumo). Al alcanzar el límite diario el programa deja de consultar OpenRouter hasta el siguiente día UTC. El comité cada cinco minutos puede mantener/revertir posiciones y volver a intentar entrada cuando la wallet está FLAT; el TP se sigue monitorizando en cada lectura de precio. `fast_entry` deja de bloquear por defecto: informa los riesgos pero no puede invalidar automáticamente un LONG/SHORT válido del comité. Para reactivar el veto estricto, define `FAST_ENTRY_ENFORCE_GATE=true`.

Perplexity Decider usa la Decisions API; Recursive Flint usa Messages API; los modelos compatibles restantes usan Chat Completions. Se detectan respuestas vacías, JSON inválido, límites y errores HTTP. Un modelo de respaldo sustituye a un voto fallido, nunca se cuenta como un voto adicional.

Consulta también [CICLO_CONTINUO_v4_2.md](CICLO_CONTINUO_v4_2.md) para el detalle del cierre por TP y la reentrada basada en un comité nuevo.

## Requisitos

- Python 3.10 o superior (recomendado 3.11).
- Flask, Requests y python-dotenv. Gunicorn solo en Render/Linux.
- Una API key de OpenRouter. No se necesitan claves ni permisos de Binance/Bybit.

## Ejecutar en Windows

Desde CMD en la carpeta del proyecto:

```bat
copy .env.example .env
notepad .env
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Pega la clave en `OPENROUTER_API_KEY` y abre `http://127.0.0.1:5000`. También puedes ejecutar `start.bat`.

### Actualizar sin perder la clave ni el historial

1. Detén la versión anterior.
2. Descomprime este ZIP en una carpeta nueva.
3. Copia el `.env` anterior y `data/prime_wave.sqlite3` a la nueva carpeta.
4. Ejecuta `start.bat`.

La app crea las tablas de cartera en la base SQLite existente automáticamente. No hace falta borrar ni migrar manualmente la base.

**Migración automática del `.env` anterior:** si `OPENROUTER_INTERVAL_SECONDS` conserva exactamente `3600` o `7200`, la nueva versión lo convierte a `300` segundos (5 minutos). Los intervalos personalizados distintos se mantienen. `AI_MIN_CONFIDENCE=62` se migra a `30`. Si quieres cambiar la frecuencia, ajusta el valor después de esta migración.

## Ejecutar en Render

```bash
pip install -r requirements-render.txt
gunicorn app:app --workers 1 --threads 4 --timeout 120
```

Configura `OPENROUTER_API_KEY` como variable secreta. `render.yaml` incluye los dos modelos principales, respaldos, cadencia y costes de simulación. Usa un solo worker porque el actualizador corre en un hilo dentro del proceso. Para conservar SQLite entre despliegues, conecta un disco persistente y define `DATA_DIR`, por ejemplo `/var/data`.

## Variables relevantes

| Variable | Predeterminado | Función |
|---|---:|---|
| `SYMBOL` | `RENDERUSDT` | Par consultado |
| `HISTORY_CANDLES` | `5000` | Velas 15m guardadas y procesadas para Elliott (~52 días) |
| `POLL_SECONDS` | `8` | Lectura del precio |
| `KLINE_REFRESH_SECONDS` | `18` | Actualización de velas |
| `OPENROUTER_API_KEY` | — | Clave secreta |
| `OPENROUTER_MODELS` | Perplexity Flash + Nemotron Lightning | Modelos principales; dos votos necesarios |
| `OPENROUTER_FALLBACK_MODELS` | Lista gratuita del usuario | Modelos de reemplazo |
| `OPENROUTER_INTERVAL_SECONDS` | `300` | Segundos entre comités; mínimo 300 |
| `OPENROUTER_MAX_COMMITTEES_PER_DAY` | `20` | Comités diarios; normalmente 2 solicitudes principales por comité |
| `AI_MIN_CONFIDENCE` | `30` | Convicción mínima individual requerida para LONG |
| `OPENROUTER_TIMEOUT_SECONDS` | `35` | Tiempo máximo por solicitud |
| `OPENROUTER_MAX_TOKENS` | `2400` | Presupuesto de salida para JSON y 20 evaluaciones de evidencia  |
| `OPENROUTER_MAX_ATTEMPTS` | `10` | Máximo de modelos probados por comité |
| `SIM_OPEN_FEE_PCT` | `0.035` | Comisión porcentual cobrada solo al abrir |
| `SIM_POSITION_SIZE_PCT` | `100` | Porcentaje de wallet asignado a la operación |
| `SIM_TAKE_PROFIT_PCT` | `0.20` | TP inicial hasta que haya muestra suficiente |
| `ADAPTIVE_MIN_SAMPLES` | `30` | Mínimo de resultados completos antes del autoajuste |
| `ADAPTIVE_LOOKBACK` | `120` | Máximo de resultados recientes para calibrar |
| `ADAPTIVE_TP_MIN_PCT` | `0.20` | Menor TP que se compara |
| `ADAPTIVE_TP_MAX_PCT` | `0.30` | Mayor TP que se compara |
| `FAST_MARKET_REFRESH_SECONDS` | `20` | Refresco de datos de entrada rápida |
| `FAST_MARKET_HISTORY_CANDLES` | `3000` | Historial 1m conservado |
| `FAST_ENTRY_REFERENCE_CANDLES` | `2400` | Histórico usado para analogías TP-first |
| `FAST_ENTRY_MIN_DIRECTIONAL_EDGE_GAP_PCT` | `4.5` | Diferencia LONG/SHORT mínima del diagnóstico rápido |
| `FAST_ENTRY_ENFORCE_GATE` | `false` | Si es `true`, el filtro fast-entry vuelve a bloquear señales no elegibles |
| `DATA_DIR` | `data` | Carpeta de SQLite |
| `MARKET_PROVIDER` | `auto` | `auto`, `binance` o `bybit` |

## API local

- `/api/state`: precio, análisis, comité LLM, wallet simulada, posición y velas.
- `/api/portfolio`: wallet en porcentaje, equity, posición activa e historial cerrado.
- `/api/history?limit=300`: velas históricas.
- `/api/health`: estado de mercado, OpenRouter y cartera simulada.

## Pruebas locales

```bash
python -m unittest discover -v
```

Las pruebas no necesitan conexión de mercado ni una clave real. Comprueban pivotes/ondas, política LONG/SHORT, cadena de respaldos, comisión de apertura, reversión, P&L, TP sin comisión de cierre, ausencia de look-ahead, filtros TP-first, separación temporal de analogías, fuerza relativa BTC/ETH, flujo taker reciente, obstáculos 1m/15m, empate direccional, mínimos de muestra, límites del autoajuste y calibración adaptativa.

## Estructura

```text
render_prime_wave/
├── app.py
├── engine.py
├── ai_analyzer.py
├── portfolio.py
├── adaptive_learning.py
├── config.py
├── .env.example
├── requirements.txt
├── requirements-render.txt
├── render.yaml
├── start.bat
├── test_engine.py
├── test_ai_analyzer.py
├── test_portfolio.py
├── test_adaptive_learning.py
├── test_wave_dimensions.py
├── templates/index.html
├── static/app.js
└── static/style.css
```
