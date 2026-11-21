\set ON_ERROR_STOP on

BEGIN;

DO $bootstrap$
DECLARE
  domain text;
  owner_role text;
  runtime_role text;
  migrator_role text;
  domains constant text[] := ARRAY[
    'cotizacion', 'identidad', 'perfilamiento', 'polizas',
    'siniestros', 'notificaciones', 'asistencias'
  ];
BEGIN
  IF current_database() <> 'solventa_prod' THEN
    RAISE EXCEPTION 'Conectar exclusivamente a solventa_prod del RDS nuevo verificado';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = ANY(domains))
     OR EXISTS (
       SELECT 1 FROM pg_roles
       WHERE rolname = ANY(
         ARRAY(
           SELECT domain_name || suffix
           FROM unnest(domains) AS domain_list(domain_name)
           CROSS JOIN unnest(ARRAY['_owner', '_runtime', '_migrator']) AS suffix_list(suffix)
         )
       )
     )
     OR EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public') THEN
    RAISE EXCEPTION 'El destino no esta vacio; detener y revisar, no sobrescribir ni reutilizar roles';
  END IF;

  REVOKE ALL ON DATABASE solventa_prod FROM PUBLIC;
  REVOKE ALL ON SCHEMA public FROM PUBLIC;

  FOREACH domain IN ARRAY domains LOOP
    owner_role := domain || '_owner';
    runtime_role := domain || '_runtime';
    migrator_role := domain || '_migrator';
    EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', owner_role);
    EXECUTE format('GRANT %I TO %I WITH SET TRUE', owner_role, current_user);
    EXECUTE format('CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', runtime_role);
    EXECUTE format('CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', migrator_role);
    EXECUTE format('GRANT CONNECT ON DATABASE solventa_prod TO %I, %I', runtime_role, migrator_role);
    EXECUTE format('ALTER ROLE %I IN DATABASE solventa_prod SET search_path = %I', runtime_role, domain);
    EXECUTE format('ALTER ROLE %I IN DATABASE solventa_prod SET search_path = %I', migrator_role, domain);
    EXECUTE format('GRANT %I TO %I WITH INHERIT FALSE, SET TRUE', owner_role, migrator_role);
    EXECUTE format('CREATE SCHEMA %I AUTHORIZATION %I', domain, owner_role);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', domain, runtime_role);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
      owner_role, domain, runtime_role
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
      owner_role, domain, runtime_role
    );
    EXECUTE format('REVOKE %I FROM %I', owner_role, current_user);
  END LOOP;
END
$bootstrap$;

COMMIT;

-- psql solicita cada password sin eco; no pasarlos mediante variables o argumentos.
\password cotizacion_runtime
\password cotizacion_migrator
\password identidad_runtime
\password identidad_migrator
\password perfilamiento_runtime
\password perfilamiento_migrator
\password polizas_runtime
\password polizas_migrator
\password siniestros_runtime
\password siniestros_migrator
\password notificaciones_runtime
\password notificaciones_migrator
\password asistencias_runtime
\password asistencias_migrator
