# CLAUDE.md

Este proyecto usa **[AGENTS.md](AGENTS.md)** como guía para agentes de IA. **Léelo antes de trabajar** en el repo: contiene la estructura, las convenciones, cómo extender (temas, fuentes, proveedores LLM) y las reglas de seguridad.

Reglas críticas (resumen):
- **Nunca** subas `.env` ni imprimas claves. Los secretos viven en `.env` (ignorado por git); en el código se leen con `os.getenv`.
- **X es de pago por uso**: respeta el tope mensual de `fetch_x` (`config/sources.yaml` → `x_twitter.monthly_read_budget`).
- Comentarios y documentación en **español**. Cada fuente debe fallar de forma aislada (warning, sin romper la corrida).

Para el resto (uso, instalación, arquitectura), ver [README.md](README.md).
