# rds-postgres

Instancia RDS PostgreSQL de una sola zona, cifrada con KMS y con credenciales gestionadas en
Secrets Manager. Reemplaza a `rds-aurora` mientras el proyecto no tenga tráfico real: `db.t4g.micro`
entra en la capa gratuita, Aurora Serverless v2 no. Para volver a Aurora basta cambiar el módulo
en el ambiente (los outputs tienen los mismos nombres).
