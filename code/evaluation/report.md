# Resultado de ejecución

- Solicitudes finales procesadas: 250; filas generadas: 250.
- Ejemplos evaluados con el mismo motor: 25.
- Imágenes locales abiertas y verificadas: 16; importes sin resolver: 0.
- Errores de validación final: 0. Cobertura, orden, esquema y unicidad: correctos.
- Pruebas de lógica: 24/24.
- API de modelos durante la ejecución final: 0 llamadas; 0 tokens; coste API USD 0.

## Coincidencia con los ejemplos

| Columna | Coincidencia |
|---|---:|
| amount_safe_to_pay | 4% (1/25) |
| affordability_status | 60% (15/25) |
| recommended_payment_method | 64% (16/25) |
| payment_plan | 60% (15/25) |
| earliest_date_for_full_payment | 48% (12/25) |
| spending_changes_needed | 76% (19/25) |

Error relativo absoluto medio del importe: 26.21%.

## Límites

La precisión de los importes exactos es baja. Los resultados son reproducibles y pasan las
reglas del simulador, pero no se afirma que estén optimizados ni que coincidan con la
evaluación oculta. La reconstrucción aplica mensajes vigentes y una política conservadora
de gastos variables; el problema no define una fórmula única para estos gastos. Algunos
ejemplos muestran fechas posteriores al plazo, mientras que esta implementación aplica
la restricción explícita del usuario. Los detalles por solicitud están en `sample_metrics.json`.

Una captura de pedido está recortada bajo el subtotal visible. Una factura médica es
provisional. Estos límites se conservan en el registro de imágenes; no se inventan cargos.
La extracción de texto libre usa patrones delimitados en inglés e indonesio y no cubre
cualquier mensaje posible. Nuevas imágenes ambiguas requieren OCR local o evidencia revisada.
La transcripción entregada es un registro de desarrollo con solicitudes y resúmenes de
respuestas; no es una exportación íntegra del historial interno de la aplicación.

Los archivos originales no fueron modificados. El repositorio creado es local.
