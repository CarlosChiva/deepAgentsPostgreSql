# Requirements: DeepAgents + PostgreSQL Dockerized Infrastructure

## Context
Crear una infraestructura dockerizada con 2 servicios principales y una lógica de backend específica.

## 1. Infraestructura (Docker Compose)
- **Servicio 1:** PostgreSQL (para almacenamiento de estado/checkpointer).
- **Servicio 2:** FastAPI (construida con `uv` como gestor de paquetes).

## 2. Lógica Backend (FastAPI)
- **Core del Chatbot:** LangChain + DeepAgents (framework específico integrado en LangChain).
- **Gestión de Estado:** Soporte para múltiples hilos de chat simultáneos usando `thread_id`.
- **Checkpointer:** El estado de los hilos debe persistirse en la base de datos PostgreSQL.

## 3. Endpoints Requeridos
- **POST /chat:** Ruta para enviar mensajes y recibir respuestas de DeepAgents (manejando el `thread_id`).
- **GET /chat/{thread_id}:** Ruta para recuperar el historial de conversaciones de un hilo específico desde la base de datos.

## Flujo de Trabajo
1. Iniciar con la estructura de directorios.
2. Crear el `docker-compose.yml` y los `Dockerfile`s necesarios (especialmente configurando `uv` para FastAPI).
3. Desarrollar el FastAPI con las rutas indicadas.
4. Implementar la integración con LangChain y DeepAgents.
5. Configurar el SQLAlchemy/AsyncSession para PostgreSQL y el checkpointer.
