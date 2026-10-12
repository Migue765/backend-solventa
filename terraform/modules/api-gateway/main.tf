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
# HTTP API + VPC Link V2
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name}-api"
  protocol_type = "HTTP"

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

resource "aws_apigatewayv2_route" "v1" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "ANY /v1/{proxy+}"
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

  tags = var.tags
}
