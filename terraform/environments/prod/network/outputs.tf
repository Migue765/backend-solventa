output "network_context" {
  description = "Solo metadatos de red para revisar y pasar explicitamente a primary; no state ni secretos."
  value = {
    account_id         = var.account_id
    region             = var.aws_region
    name_prefix        = var.name_prefix
    vpc_id             = module.network.vpc_id
    vpc_arn            = "arn:aws:ec2:${var.aws_region}:${var.account_id}:vpc/${module.network.vpc_id}"
    public_subnet_ids  = module.network.public_subnet_ids
    private_subnet_ids = module.network.private_subnet_ids
    security_group_ids = {
      db       = aws_security_group.db.id
      redis    = aws_security_group.redis.id
      alb      = aws_security_group.alb.id
      vpc_link = aws_security_group.vpc_link.id
      cicd     = { for service, group in aws_security_group.cicd : service => group.id }
    }
  }
}
