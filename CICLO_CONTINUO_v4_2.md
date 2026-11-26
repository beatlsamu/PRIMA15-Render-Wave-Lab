> Nota de vigencia: este documento describe la implementación original v4.2. Desde v4.4.0 el comité normal se ejecuta cada 5 minutos por defecto (300 s), y fast-entry es orientativo salvo configuración estricta.

# PRIMA-15 v4.2 — Ciclo continuo de operaciones simuladas

## Qué ocurría en v4.1

El Take Profit se comprobaba en cada sondeo del precio. Sin embargo, al cerrarse una posición, OpenRouter solo se consultaba si también había vencido `OPENROUTER_INTERVAL_SECONDS` (300 segundos por defecto en las versiones actuales). Por eso la wallet podía permanecer sin posición hasta una hora. Además, si la consulta coincidía con el cierre, el código aplazaba la acción de cartera, aunque el snapshot se había construido después del cierre y ya mostraba la wallet en estado FLAT.

## Qué hace v4.2

1. En cada sondeo se obtiene un precio reciente y se comprueba el Take Profit de la posición simulada.
2. Si no se alcanza el TP, la posición sigue abierta; no se cobra otra comisión.
3. Cuando se alcanza el TP, se cierra la posición sin comisión de cierre y se guarda la operación.
4. El cierre fuerza una evaluación OpenRouter inmediata, aunque no haya vencido el intervalo normal.
5. El snapshot se construye después del cierre. Por tanto, los LLM ven el precio actual, la wallet ya sin posición y la operación cerrada dentro de su historial reciente.
6. Si el comité devuelve `status=ok`, dos o más modelos válidos (quórum requerido: 2) y una dirección `LONG` o `SHORT`, el programa abre esa nueva posición en el mismo ciclo al precio reciente. Se cobra el 0,035% solo por esta nueva apertura.
7. Si el comité devuelve `WAIT`, falla un proveedor, la respuesta está incompleta o falta quórum, la cartera permanece FLAT. No se reutiliza la última dirección anterior al Take Profit.
8. Si no hay API key, precio fresco o conexión de mercado, no se intenta una entrada.

## Cuándo se vuelve a consultar después de un fallo

El intento inmediato posterior al Take Profit cuenta como una evaluación del comité y actualiza el temporizador normal. Si falla OpenRouter o no existe quórum, el programa espera al siguiente intervalo configurado (`OPENROUTER_INTERVAL_SECONDS`, 300 segundos por defecto en las versiones actuales). Esto evita ciclos rápidos de llamadas y consumo excesivo de modelos gratuitos.

## Comisión y cartera

- El cierre por TP no cobra comisión.
- La apertura siguiente descuenta una comisión de 0,035% del saldo disponible.
- Si el comité no entrega una dirección válida, no se abre posición ni se cobra comisión.
- Se mantiene una sola posición abierta a la vez.

## Verificación

La suite incorpora pruebas de transición TP → FLAT → nueva evaluación → nueva apertura, además de pruebas para impedir una entrada con falta de API, precio no fresco, desconexión, `WAIT` o quórum insuficiente.
