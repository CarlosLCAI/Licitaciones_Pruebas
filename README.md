# Monitor de Licitaciones PLACSP — CAI Consultores

Sistema automatizado de vigilancia diaria de licitaciones públicas publicadas en la Plataforma de Contratación del Sector Público (PLACSP), filtradas por ámbito geográfico, materia (CPV) y estado de tramitación. Notifica a Microsoft Teams y expone un visor web público con el histórico de resultados.

## Qué hace

Cada ejecución:

1. Descarga el feed Atom de sindicación nacional de PLACSP (`sindicacion_643`).
2. Pagina siguiendo el enlace `rel="next"` del feed hasta cubrir la ventana temporal configurada.
3. Filtra las licitaciones por código NUTS, códigos CPV y estado de tramitación.
4. Deduplica frente a lo ya detectado en ejecuciones anteriores.
5. Publica un aviso en un canal de Microsoft Teams con el resumen y enlace al visor.
6. Actualiza el histórico acumulado, consultable en un visor web público.

## Filtro activo — Filtro 1 · Diseño Urbano Andalucía

| Criterio | Valor |
|---|---|
| Región (NUTS) | `ES61` — Andalucía (todas las provincias) |
| CPV admitidos | Ver `filtro_config.json` — pendientes de confirmación definitiva por el usuario |
| Estados admitidos | `PUB` (publicada, en plazo de presentación) |
| Ventana de lectura | 30 horas desde el momento de ejecución |

Los códigos CPV configurados son una selección orientativa de servicios de urbanismo, arquitectura, ingeniería y planificación territorial. Deben revisarse y confirmarse formalmente antes de considerar el filtro cerrado.

## Próximos pasos (roadmap)
- **[✓]Estadísticas**:
- **[✓]Múltiples filtros configurables**: permitir varios filtros simultáneos (Filtro 2, 3, 4...) con distinto ámbito geográfico, CPV o estado, cada uno generando su propio histórico, seleccionables desde el desplegable ya presente en el visor.
- **[✓]Edición de filtros desde el front-end**: requiere incorporar un backend (API propia) que reciba los cambios de configuración desde el visor y los traslade al proceso de captura, sustituyendo el modelo actual de solo lectura.
- **[~]Análisis automático de viabilidad con IA**: para cada licitación filtrada, descargar el PCAP y el PPT, extraer los requisitos de solvencia técnica y económica, contrastarlos con los criterios de la empresa, y generar un resumen de los criterios de adjudicación junto con una valoración de viabilidad de presentarse.
