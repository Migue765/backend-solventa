locals {
  https         = var.listener_protocol == "HTTPS"
  listener_port = local.https ? 443 : 80
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------
resource "aws_security_group" "vpc_link" {
  name        = "${var.name}-vpclink"
  description = "VPC Link de API Gateway hacia el ALB interno"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-vpclink" })
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb-internal"
  description = "ALB interno: solo acepta trafico del VPC Link"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-alb-internal" })
}

resource "aws_vpc_security_group_egress_rule" "vpc_link_to_alb" {
  security_group_id            = aws_security_group.vpc_link.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = local.listener_port
  to_port                      = local.listener_port
  description                  = "VPC Link hacia ALB"
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_vpc_link" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.vpc_link.id
  ip_protocol                  = "tcp"
  from_port                    = local.listener_port
  to_port                      = local.listener_port
  description                  = "Desde VPC Link"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_targets" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = var.target_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = var.target_port
  to_port                      = var.target_port
  description                  = "ALB hacia pods"
}

resource "aws_vpc_security_group_ingress_rule" "targets_from_alb" {
  security_group_id            = var.target_security_group_id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.target_port
  to_port                      = var.target_port
  description                  = "Desde ALB interno (${var.name})"
}

# ---------------------------------------------------------------------------
# ALB interno + target group (tipo ip) que se enlaza con un TargetGroupBinding
# ---------------------------------------------------------------------------
resource "aws_lb" "internal" {
  name                       = "${var.name}-internal"
  internal                   = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.private_subnet_ids
  drop_invalid_header_fields = true

  tags = merge(var.tags, { Name = "${var.name}-internal" })
}

resource "aws_lb_target_group" "service" {
  name        = "${var.name}-cotizacion"
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-cotizacion" })
}

resource "aws_lb_listener" "this" {
  load_balancer_arn = aws_lb.internal.arn
  port              = local.listener_port
  protocol          = var.listener_protocol
  ssl_policy        = local.https ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn   = local.https ? var.certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service.arn
  }

  lifecycle {
    precondition {
      condition     = !local.https || var.certificate_arn != null
      error_message = "certificate_arn es obligatorio cuando listener_protocol = HTTPS."
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# bff-web: segundo target group tras el mismo ALB. El listener sigue enviando
# por defecto a cotizacion; solo las rutas del BFF se desvian por path.
# ---------------------------------------------------------------------------
resource "aws_lb_target_group" "bff_web" {
  name        = "${var.name}-bff-web"
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-bff-web" })
}

resource "aws_lb_listener_rule" "bff_web" {
  listener_arn = aws_lb_listener.this.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.bff_web.arn
  }

  condition {
    path_pattern {
      values = var.bff_web_path_patterns
    }
  }

  tags = var.tags
}

resource "aws_lb_listener_rule" "bff_web_consents" {
  listener_arn = aws_lb_listener.this.arn
  priority     = 11

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.bff_web.arn
  }

  condition {
    path_pattern {
      values = ["/v1/consentimientos/perfilamiento"]
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# bff-movil: tercer target group tras el mismo ALB; todo /movil/* se desvia a el.
# ---------------------------------------------------------------------------
resource "aws_lb_target_group" "bff_movil" {
  name        = "${var.name}-bff-movil"
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-bff-movil" })
}

resource "aws_lb_listener_rule" "bff_movil" {
  listener_arn = aws_lb_listener.this.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.bff_movil.arn
  }

  condition {
    path_pattern {
      values = var.bff_movil_path_patterns
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# identidad: cuarto target group. Solo expone publicamente el discovery OIDC y el JWKS
# (/.well-known/*); los BFF le hablan por el DNS interno del cluster, no por el gateway.
# ---------------------------------------------------------------------------
resource "aws_lb_target_group" "identidad" {
  name        = "${var.name}-identidad"
  port        = var.target_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-identidad" })
}

resource "aws_lb_listener_rule" "identidad" {
  listener_arn = aws_lb_listener.this.arn
  priority     = 30

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.identidad.arn
  }

  condition {
    path_pattern {
      values = var.identidad_path_patterns
    }
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# HTTP API + VPC Link V2
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name}-api"
  protocol_type = "HTTP"

  # El gateway responde solo los preflight OPTIONS (sin pasar por el authorizer).
  cors_configuration {
    allow_origins     = var.cors_allowed_origins
    allow_methods     = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers     = ["authorization", "content-type", "idempotency-key", "x-correlation-id"]
    expose_headers    = ["x-correlation-id", "retry-after"]
    max_age           = 3600
    allow_credentials = false
  }

  tags = var.tags
}

resource "aws_apigatewayv2_vpc_link" "this" {
  name               = "${var.name}-vpclink"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = var.private_subnet_ids

  tags = var.tags
}

resource "aws_apigatewayv2_integration" "alb" {
  api_id             = aws_apigatewayv2_api.this.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = aws_lb_listener.this.arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.this.id

  payload_format_version = "1.0"

  dynamic "tls_config" {
    for_each = local.https && var.tls_server_name != null ? [1] : []

    content {
      server_name_to_verify = var.tls_server_name
    }
  }
}

# Validador JWT nativo del HTTP API. El emisor es la propia URL del API: el gateway descarga
# {issuer}/.well-known/openid-configuration y el JWKS (rutas publicas hacia identidad, mas abajo).
# Si identidad no sirve el discovery, TODAS las rutas protegidas responden 401.
resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.this.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.name}-jwt"

  jwt_configuration {
    audience = [var.jwt_audience]
    issuer   = aws_apigatewayv2_api.this.api_endpoint
  }
}

# ---- Rutas protegidas: exigen un access token valido ------------------------------------------
resource "aws_apigatewayv2_route" "v1" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "ANY /v1/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.alb.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_apigatewayv2_route" "movil" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "ANY /movil/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.alb.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

# ---- Rutas publicas (mas especificas que el proxy protegido) ----------------------------------
# Autenticacion y registro, con throttling propio para frenar la fuerza bruta.
resource "aws_apigatewayv2_route" "login" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /v1/sesiones"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "renew" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /v1/sesiones/renovar"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "register" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /v1/usuarios"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "movil_login" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /movil/v1/sesiones"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "movil_renew" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /movil/v1/sesiones/renovar"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "movil_register" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /movil/v1/usuarios"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

# Discovery OIDC y JWKS: los consume el authorizer del gateway y cualquier cliente que valide tokens.
resource "aws_apigatewayv2_route" "oidc_discovery" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /.well-known/openid-configuration"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "jwks" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /.well-known/jwks.json"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "ping" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /ping"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.login.route_key
    throttling_rate_limit  = var.auth_throttling_rate_limit
    throttling_burst_limit = var.auth_throttling_burst_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.renew.route_key
    throttling_rate_limit  = var.auth_throttling_rate_limit
    throttling_burst_limit = var.auth_throttling_burst_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.register.route_key
    throttling_rate_limit  = var.auth_throttling_rate_limit
    throttling_burst_limit = var.auth_throttling_burst_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.movil_login.route_key
    throttling_rate_limit  = var.auth_throttling_rate_limit
    throttling_burst_limit = var.auth_throttling_burst_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.movil_renew.route_key
    throttling_rate_limit  = var.auth_throttling_rate_limit
    throttling_burst_limit = var.auth_throttling_burst_limit
  }

  route_settings {
    route_key              = aws_apigatewayv2_route.movil_register.route_key
    throttling_rate_limit  = var.auth_throttling_rate_limit
    throttling_burst_limit = var.auth_throttling_burst_limit
  }

  tags = var.tags
}
