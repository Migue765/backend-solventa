# elasticache-redis

Redis (ElastiCache) para sesiones y rate limit de `bff-web`.

- Replication group de un solo nodo (`cache.t4g.micro`), sin failover automatico (las sesiones son efimeras).
- Subnet group en subnets privadas; el SG solo admite 6379 desde los SGs indicados (nodos EKS).
- Cifrado en transito (`rediss://`) y en reposo con la CMK del entorno.
- Auth token generado con `random_password` y guardado en Secrets Manager (`<name>-redis-auth`) como JSON:
  `{"auth_token", "host", "port", "url"}`. `url` ya es el `REDIS_URL` listo para la app.

Si el nodo se reinicia o se reemplaza, las sesiones activas se pierden y los usuarios deben iniciar sesion de nuevo.
Para alta disponibilidad: `num_cache_clusters = 2` y `automatic_failover_enabled = true`.
