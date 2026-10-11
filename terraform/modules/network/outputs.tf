output "vpc_id" {
  description = "ID de la VPC."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "CIDR de la VPC."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "IDs de subnets publicas."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs de subnets privadas."
  value       = aws_subnet.private[*].id
}

output "availability_zones" {
  description = "AZs usadas."
  value       = local.azs
}

output "nat_gateway_id" {
  description = "ID de la NAT gateway."
  value       = aws_nat_gateway.this.id
}
