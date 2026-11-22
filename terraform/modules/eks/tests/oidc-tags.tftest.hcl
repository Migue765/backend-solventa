provider "aws" {
  region                      = "us-east-2"
  access_key                  = "testing"
  secret_key                  = "testing"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  default_tags {
    tags = {
      Project     = "solventa"
      Environment = "prod"
      ManagedBy   = "terraform"
      Component   = "backend"
    }
  }
}

provider "aws" {
  alias                       = "explicit_tags"
  region                      = "us-east-2"
  access_key                  = "testing"
  secret_key                  = "testing"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

override_data {
  target = data.aws_partition.current
  values = { partition = "aws", dns_suffix = "amazonaws.com" }
}

override_data {
  target = data.tls_certificate.oidc
  values = {
    certificates = [{ sha1_fingerprint = "0000000000000000000000000000000000000001" }]
  }
}

variables {
  cluster_name         = "solventa-prod-eks"
  vpc_id               = "vpc-0123456789abcdef0"
  private_subnet_ids   = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  public_access_cidrs  = ["192.0.2.1/32"]
  admin_principal_arns = ["arn:aws:iam::396383602233:role/test-only"]
  tags = {
    Project     = "solventa"
    Environment = "prod"
    ManagedBy   = "terraform"
    Component   = "backend"
  }
}

run "legacy_null_keeps_tags" {
  command = plan

  assert {
    condition = (
      var.oidc_provider_tags == null &&
      aws_iam_openid_connect_provider.this.tags == var.tags &&
      aws_iam_openid_connect_provider.this.tags_all == var.tags
    )
    error_message = "El default null debe conservar tags y tags_all de callers anteriores."
  }
}

run "empty_tags_alone_does_not_remove_defaults" {
  command = plan
  variables {
    oidc_provider_tags = {}
  }

  assert {
    condition = (
      length(coalesce(aws_iam_openid_connect_provider.this.tags, {})) == 0 &&
      aws_iam_openid_connect_provider.this.tags_all == var.tags
    )
    error_message = "Con default_tags el provider sigue etiquetando OIDC aunque tags sea {}."
  }
}

run "primary_alias_keeps_explicit_tags" {
  command = plan
  providers = {
    aws = aws.explicit_tags
  }
  variables {
    oidc_provider_tags = {}
    additional_node_groups = {
      extra = {
        instance_types  = ["t3.small"]
        release_version = "1.35.9-20261119"
      }
    }
  }

  assert {
    # AWS 5.100 deja tags_all OIDC y thumbprints desconocidos hasta apply; no simularlos.
    condition = (
      length(coalesce(aws_iam_openid_connect_provider.this.tags, {})) == 0 &&
      toset(aws_iam_openid_connect_provider.this.client_id_list) == toset(["sts.amazonaws.com"])
    )
    error_message = "OIDC no debe solicitar tags explicitos ni cambiar client ID."
  }

  assert {
    condition = alltrue([
      for tags in concat([
        aws_iam_role.cluster.tags, aws_iam_role.cluster.tags_all,
        aws_iam_role.node.tags, aws_iam_role.node.tags_all,
        aws_eks_access_entry.admin[0].tags, aws_eks_access_entry.admin[0].tags_all,
        aws_launch_template.node.tags, aws_launch_template.node.tags_all,
        aws_eks_node_group.default.tags, aws_eks_node_group.default.tags_all,
        ],
        flatten([for group in values(aws_eks_node_group.additional) : [group.tags, group.tags_all]]),
      flatten([for addon in values(aws_eks_addon.this) : [addon.tags, addon.tags_all]])) :
      tags == var.tags
    ])
    error_message = "Conservar los cuatro tags efectivos de AMBOS roles, access entry, plantilla, nodos y TODOS los addons."
  }

  assert {
    condition = (
      aws_eks_cluster.this.tags == tomap(merge(var.tags, { Name = var.cluster_name })) &&
      aws_eks_cluster.this.tags_all == tomap(merge(var.tags, { Name = var.cluster_name })) &&
      length(aws_eks_node_group.additional) == 1 &&
      length(aws_eks_addon.this) == 3
    )
    error_message = "El cluster conserva Name y los cuatro tags, sin omitir addons del recorrido."
  }
}
