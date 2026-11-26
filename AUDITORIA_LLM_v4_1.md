# Auditoría LLM de PRIMA-15 v4.1

## Resultado de la revisión

La versión 4.1 amplía el snapshot que se envía a OpenRouter y obliga a cada modelo a clasificar las 16 familias de evidencia. La auditoría comprueba la cobertura de la respuesta y la identidad del snapshot enviado; no puede demostrar los procesos internos o la atención real del modelo a cada dato.

## Flujo de información

1. El monitor reúne el precio actual, el proveedor y los datos de mercado de 24 horas.
2. El motor determinista analiza la ventana activa de hasta 350 velas de 15 minutos.
3. `build_snapshot` reúne el resultado íntegro del motor, los tres paquetes de análisis de ondas, la información de mechas, la cartera, el backtest, el aprendizaje, el historial de predicciones y la política vigente.
4. El mismo snapshot JSON se entrega a cada modelo consultado. Las instrucciones cambian según el puesto: Perplexity ocupa el puesto de excepción/rechazo y Nemotron el de continuidad.
5. Cada respuesta debe completar las 16 clasificaciones. Un voto incompleto o con una opción inválida se rechaza y se intenta un respaldo en el mismo puesto.
6. Cada voto aceptado registra SHA-256 del snapshot, tamaño en bytes, modelo solicitado/servido, cobertura y tipo de evaluación.

## Datos incluidos en cada snapshot

- Símbolo, intervalo, precio, hora, proveedor y mercado de 24 horas.
- Todas las claves del resultado de `engine.analyze`, con las dimensiones y el diagnóstico de mechas incluidos como secciones completas separadas.
- Todas las escalas temporales primas, todas las piernas de amplitud y todos los casos analógicos generados por la ventana activa.
- Todas las lecturas de mechas por escala, P15, pivotes, puntuaciones, razones y bloqueadores calculados.
- Hasta 350 velas OHLCV de 15 minutos, incluyendo volumen cotizado, número de operaciones, tiempo de cierre y marca de vela cerrada/abierta.
- Política operativa actual: quórum, roles, umbral, continuidad, excepción, resolución de incertidumbre, WAIT por fallo, comisión, exposición, TP y prohibición de órdenes reales.
- Wallet simulada en porcentajes, posición, P&L, equity, costes y hasta 20 operaciones cerradas recientes.
- Resumen de aprendizaje, resumen de backtest y hasta 20 ejemplos etiquetados.
- Hasta 20 señales técnicas recientes y hasta 12 predicciones previas con resultados futuros que ya se hayan completado.
- Perfil adaptativo completo: pesos, muestras, umbral, TP, evaluación de modelos/roles, guardrails y hasta seis cambios recientes.
- Un manifiesto que enumera lo enviado, los límites, los recuentos y lo que se excluyó deliberadamente.

## Las 16 familias con cobertura obligatoria

| Familia | Qué debe clasificar el modelo |
|---|---|
| `market` | Contexto y variación de mercado 24h |
| `raw_candles` | Dirección, rango y ritmo de las velas incluidas |
| `engine` | Señal, puntuaciones, razones, pivotes y bloqueadores del motor |
| `policy` | Alineación o conflicto con la política operativa |
| `temporal` | Posición de precio y alineación en todas las escalas temporales |
| `prime` | Coincidencias de tiempos, ventanas y proporciones primas |
| `amplitude` | Amplitud porcentual, velocidad, expansión/contracción y retrocesos |
| `behavior` | Huellas y analogías históricas con outcomes resueltos |
| `wick` | P15, geometría, escalas, barrido y rechazo confirmado/no confirmado |
| `trend` | Persistencia en horizontes 3/5/8 y porcentaje de acuerdo |
| `signals` | Señales recientes y sus resultados observados |
| `backtest` | Muestras, acierto, retorno, MFE y MAE |
| `prior_memory` | Predicciones LLM anteriores y outcomes disponibles |
| `portfolio` | Wallet, P&L, posición, exposición y comisiones porcentuales |
| `learning` | Estadísticas walk-forward, tamaño de muestra y resultados |
| `adaptive_profile` | Pesos, umbrales, TP, fiabilidad y cambios adaptativos |

Si una familia falta, la respuesta no se cuenta como voto válido. La cobertura 100% significa que el modelo devolvió las 16 clasificaciones en el formato requerido; no equivale a demostrar comprensión perfecta o capacidad predictiva.

## Diferencia entre modelos

### Perplexity Decider Flash / Decider

Usa la Decisions API. Recibe un `state` JSON y responde 17 preguntas tipadas en una única solicitud: una dirección (LONG/SHORT/WAIT) y una pregunta por cada familia de evidencia. Devuelve opciones y distribuciones de probabilidades. No es un modelo de conversación y no ofrece una explicación narrativa ni una cadena de razonamiento; la aplicación conserva las distribuciones como evidencia estructurada.

### Nemotron 3.5 Lightning y modelos conversacionales de respaldo

Reciben el snapshot JSON mediante Chat Completions. Se les exige devolver un JSON con dirección y una lectura `reading` más una justificación `basis` para cada una de las 16 familias. El programa valida el JSON, las opciones y la presencia de bases; una respuesta incompleta se descarta. El endpoint gratuito de Nemotron no garantiza `response_format`, por lo que la validación se hace en Python.

### Respaldos

Un respaldo reemplaza un puesto fallido y conserva el rol semántico (excepción o continuidad). Nunca suma un voto adicional. El comité necesita dos votos válidos independientes.

## Límites: qué no se envía

- No se envían todas las 3.000 velas guardadas en SQLite una por una. Se remiten las últimas 350 usadas por el motor actual y los agregados/backtests disponibles.
- Los historiales auxiliares están limitados a los registros recientes indicados: 20 operaciones, 20 ejemplos de backtest, 20 señales, 12 predicciones previas y seis cambios del perfil.
- No se envían claves API, variables secretas, archivos `.env`, logs internos, trazas de depuración ni el estado visual del navegador.
- Los resultados que todavía no han madurado no se presentan como outcomes conocidos. Si falta información, el modelo debe usar `INSUFFICIENT` o `UNAVAILABLE`.

## Auditoría de igualdad de contexto

El código serializa el snapshot una vez de forma determinista para calcular el SHA-256. Cada voto aceptado se asocia con ese mismo hash y el tamaño en bytes. Las instrucciones de rol son diferentes, pero el contenido de mercado y simulación no cambia entre puestos. El panel muestra el tamaño del snapshot, el hash, el número de velas, recuentos de escalas/pivotes/ondas/mechas/casos, registros auxiliares y cobertura por modelo.

En una prueba sintética con 3.000 velas almacenadas, la ventana activa produjo un snapshot de aproximadamente 84 KB: 350 velas, 48 lecturas temporales, 48 lecturas de mechas, ocho piernas de amplitud, tres horizontes analógicos con 18 casos y 12 pivotes. Estos recuentos dependen del mercado y de los datos disponibles; el manifiesto reporta los valores reales de cada ciclo.

## Capacidad y precauciones de OpenRouter

Las fichas consultadas publican una ventana de contexto de 262.144 tokens para [Perplexity Decider Flash V1.3](https://openrouter.ai/perplexity/pplx-decider-flash-v1.3%3Afree) y 1.000.000 de tokens para [Nemotron 3.5 Lightning (free)](https://openrouter.ai/nvidia/nemotron-3.5-lightning%3Afree). El snapshot de prueba queda holgadamente por debajo de esas ventanas publicadas. Eso no garantiza que la llamada esté disponible: los endpoints gratuitos siguen sujetos a límites, proveedores y errores temporales.

La ficha del endpoint gratuito de Nemotron advierte que la sesión puede registrarse por seguridad y para mejorar los productos de NVIDIA. Por esa razón el programa excluye credenciales y datos personales; aun así, el usuario debe revisar las condiciones del proveedor antes de enviar información que considere privada.

## Pruebas realizadas y límites de verificación

- 59 pruebas unitarias pasan: snapshot completo, 16 familias obligatorias, rutas API, respaldos, roles, igualdad de hash, cartera, aprendizaje y motor de ondas.
- `python -m py_compile *.py` pasa.
- `node --check static/app.js` pasa.
- Prueba sintética del snapshot: 84.210 bytes; 350 velas; 48 escalas temporales; 48 lecturas de mechas; ocho piernas de amplitud; 18 casos analógicos; 12 pivotes; 20 ejemplos de backtest; 20 señales; 12 predicciones previas y 16 familias obligatorias.
- No se realizó una llamada real a la cuenta OpenRouter ni a un exchange. En este entorno Flask no está instalado, por lo que tampoco se verificó el arranque integral de la app.
