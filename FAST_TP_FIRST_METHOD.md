# PRIMA-15 v4.4.0 — Diagnóstico rápido TP-first

## Objetivo

Buscar entradas simuladas que históricamente hayan alcanzado primero el Take Profit configurado, antes de tocar una barrera adversa de investigación, dentro de un horizonte corto. El motor no promete ni calcula una probabilidad calibrada de la próxima operación: usa analogías históricas filtradas, microestructura actual y un comité LLM.

## Datos usados

- Hasta 3.000 velas Spot de 1 minuto de RENDERUSDT; hasta 2.400 velas cerradas para construir analogías.
- Retornos a 1, 3, 5 y 10 minutos.
- EMA 9/21, separación y pendiente de medias, aceleración, RSI 7, VWAP 20, eficiencia de recorrido, ubicación en rango, ATR 1m y relación de volumen.
- Order book reciente: spread y desequilibrio de profundidad ponderado.
- Operaciones agresoras recientes: imbalance taker BUY/SELL, filtrado a los últimos 45 segundos.
- Retornos BTCUSDT y ETHUSDT en 3/5/10 minutos; fuerza relativa de RENDER frente al promedio de ambos.
- Obstáculos: máximos/mínimos recientes de 1m y soporte/resistencia de la ventana prima 31 del motor de 15m.

## Etiquetado histórico

Para cada instante elegible se proyecta el recorrido futuro, sin usarlo como característica de entrada:

- TP inicial: +0,20% en el precio, aunque el perfil adaptativo puede cambiarlo hasta +0,30%.
- Barrera adversa de investigación: 0,12% en contra.
- Horizonte de observación: 20 velas de un minuto.
- Si ambas barreras aparecen dentro de la misma vela OHLC, se clasifica conservadoramente como pérdida porque no se conoce el orden intravela.
- Si no toca ninguna dentro del horizonte, se marca `NO_TOUCH_WITHIN_HORIZON`.

La barrera adversa solo etiqueta analogías; no es un stop-loss real ni cierra una posición.

## Analogías

El estado actual se compara con vectores normalizados del histórico. Se seleccionan hasta 50 vecinos de similitud suficiente, pero dos muestras elegidas deben estar separadas al menos 20 minutos. Esto reduce la pseudorreplicación de velas contiguas de un mismo movimiento. La tasa TP-first se suaviza con un prior neutral y se acompaña de un límite inferior Wilson aproximado al 80%.

## Presión viva

Combina momentum 1m, 3m, 5m y 10m, cruce EMA, pendiente/aceleración, VWAP, RSI, pulso de volumen, imbalance de libro, agresión taker y contexto BTC/ETH. El resultado `long_score`/`short_score` es una puntuación direccional 0–100, no una probabilidad.

El contexto de BTC/ETH no actúa como veto mecánico: si los líderes caen pero RENDER los supera por al menos 0,08 puntos porcentuales en 5m, se reconoce desacople relativo positivo; la lógica simétrica se aplica a RENDER rezagado durante subidas de los líderes.

## Reglas del diagnóstico de elegibilidad (no bloqueantes por defecto)

- Mínimo 25 analogías temporalmente separadas.
- TP-first histórico suavizado mínimo: 62%.
- Límite Wilson inferior mínimo: 50%.
- Presión viva del lado: 58/100.
- Order book y flujo taker disponibles y recientes.
- Spread máximo: 8 bps.
- Mediana histórica hasta TP: 15 minutos o menos.
- Espacio al obstáculo superior/inferior: al menos 112% del TP requerido.
- Ventaja entre calidad LONG y SHORT: al menos 4,5 puntos; si hay empate, no se fuerza dirección.
- El objetivo neto estimado debe seguir siendo positivo después de comisión de apertura y spread estimado.

La puntuación de calidad combina 68% de tasa TP-first histórica y 32% de presión viva. Los filtros describen cuándo el setup tiene calidad cuantitativa completa; en v4.4.0 son advertencias para el comité y no vetan por defecto una decisión direccional válida. El veto solo se activa con `FAST_ENTRY_ENFORCE_GATE=true`.

## Rol de los LLM

Perplexity y Nemotron reciben el snapshot con el resumen fast-entry, velas 1m recientes, microestructura, BTC/ETH y resto de las 20 familias de evidencia. El comité usa el resultado TP-first para comprender los riesgos y explicar por qué una entrada es o no elegible. En el modo predeterminado, `eligible=false` no transforma automáticamente LONG/SHORT en WAIT. La decisión operativa exige dos respuestas LLM válidas, precio fresco y una dirección final del comité. `FAST_ENTRY_ENFORCE_GATE=true` restaura el veto estricto.

## Actualización y límites

- Precio principal: cada 8 segundos.
- Velas 1m, libro, trades y BTC/ETH: aproximadamente cada 20 segundos.
- La consulta LLM ocurre cada cinco minutos por defecto, incluso si no existe un candidato fast-entry, y también inmediatamente después del TP. El límite es de 20 comités diarios por defecto.
- El cambio de proveedor invalida la caché de libro, trades, cruces y analogías para evitar mezcla entre exchanges.

## Riesgos y validación

La simulación OHLC no reproduce el orden exacto intravela, latencia, colas, deslizamiento ni cancelaciones de órdenes del libro. La liquidez puede variar y las analogías pueden cambiar de régimen. Las tasas históricas son descriptivas; deben contrastarse con operaciones paper en vivo, muestra amplia, rendimiento neto tras costes y comparación walk-forward antes de confiar en ellas.


## Cambio operativo v4.4

El análisis TP-first se conserva íntegro como indicador histórico y advertencia, pero ya no anula por defecto una dirección LONG/SHORT válida del comité. El veto estricto es opcional mediante `FAST_ENTRY_ENFORCE_GATE=true`. Esto evita que la ausencia de order book, flujo taker o suficientes analogías deje al sistema en WAIT indefinidamente. La frecuencia normal del comité pasa a 300 segundos y el límite inicial sigue siendo 20 comités por día.
