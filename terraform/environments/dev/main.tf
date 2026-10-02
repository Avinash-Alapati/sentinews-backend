terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  # Local state default; or single S3 bucket with use_lockfile:
  # backend "s3" {
  #   bucket       = "sentinews-terraform-state-mumbai"
  #   key          = "dev/terraform.tfstate"
  #   region       = "ap-south-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "SentiNews"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

resource "random_id" "suffix" {
  byte_length = 4
}

# ==============================================================================
# 1. Minimal Networking (1 Public Subnet, No NAT Gateway = ~$32/month saved)
# ==============================================================================

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "sentinews-${var.environment}-vpc"
  }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "sentinews-${var.environment}-igw"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = {
    Name = "sentinews-${var.environment}-public-subnet"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }

  tags = {
    Name = "sentinews-${var.environment}-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ==============================================================================
# 2. Security Group (Port 80/443 Only, No SSH; Use SSM Session Manager)
# ==============================================================================

resource "aws_security_group" "ec2" {
  name        = "sentinews-${var.environment}-ec2-sg"
  description = "Public HTTP/HTTPS traffic to Caddy reverse proxy only. No inbound SSH."
  vpc_id      = aws_vpc.main.id

  ingress {
    description      = "HTTP from Internet (redirects to HTTPS in Caddy)"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    description      = "HTTPS from Internet (TLS terminated by Caddy)"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  egress {
    description      = "Allow all outbound traffic for API, RSS fetching, and updates"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name = "sentinews-${var.environment}-ec2-sg"
  }
}

# ==============================================================================
# 3. IAM Role & Instance Profile (SSM Session Manager, ECR Pull, S3, SSM Secrets)
# ==============================================================================

resource "aws_iam_role" "ec2_role" {
  name = "sentinews-${var.environment}-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

# Attach AWS managed policy for SSM Session Manager (eliminates SSH keys and open port 22)
resource "aws_iam_role_policy_attachment" "ssm_managed" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Attach AWS managed policy for ECR read/pull access
resource "aws_iam_role_policy_attachment" "ecr_readonly" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Custom policy for S3 database backups and SSM Parameter Store secrets read
resource "aws_iam_policy" "ec2_custom_policy" {
  name        = "sentinews-${var.environment}-ec2-custom-policy"
  description = "Allows access to S3 backup bucket and SSM Parameter Store secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BackupBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          aws_s3_bucket.backups.arn,
          "${aws_s3_bucket.backups.arn}/*"
        ]
      },
      {
        Sid    = "SSMParameterStoreRead"
        Effect = "Allow"
        Action = [
          "ssm:GetParameters",
          "ssm:GetParameter",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/sentinews/${var.environment}/*"
      },
      {
        Sid    = "KMSDecryptSSMSecrets"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "custom_policy_attachment" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.ec2_custom_policy.arn
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "sentinews-${var.environment}-ec2-profile"
  role = aws_iam_role.ec2_role.name
}

# ==============================================================================
# 4. S3 Database Backup Bucket with 14-Day Lifecycle Expiry
# ==============================================================================

resource "aws_s3_bucket" "backups" {
  bucket        = "sentinews-backups-${var.environment}-${random_id.suffix.hex}"
  force_destroy = false

  tags = {
    Name = "sentinews-${var.environment}-backups"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups_encryption" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backups_public_block" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups_lifecycle" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "expire-nightly-backups-after-14-days"
    status = "Enabled"

    expiration {
      days = 14
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

# ==============================================================================
# 5. ECR Repository with 5-Image Lifecycle Policy
# ==============================================================================

resource "aws_ecr_repository" "app" {
  name                 = "sentinews-backend-${var.environment}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "sentinews-backend-${var.environment}"
  }
}

resource "aws_ecr_lifecycle_policy" "app_policy" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep only last 5 tagged images to minimize storage cost"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "latest", "sha-", "prod-", "dev-"]
          countType     = "imageCountMoreThan"
          countNumber   = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ==============================================================================
# 6. EC2 Instance (Ubuntu 22.04 LTS, Docker Compose, 20GB gp3 EBS)
# ==============================================================================

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = [startswith(var.instance_type, "t4g") ? "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-arm64-server-*" : "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "app_server" {
  ami                  = data.aws_ami.ubuntu.id
  instance_type        = var.instance_type
  subnet_id            = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.ec2.id]
  iam_instance_profile = aws_iam_instance_profile.ec2_profile.name

  root_block_device {
    volume_size           = var.root_volume_size_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name = "sentinews-${var.environment}-root-volume"
    }
  }

  user_data = <<-EOF
              #!/bin/bash
              set -euo pipefail

              # Update packages and install Docker
              apt-get update -y
              apt-get install -y ca-certificates curl gnupg lsb-release awscli

              mkdir -p /etc/apt/keyrings
              curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg

              echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

              apt-get update -y
              apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

              systemctl enable docker
              systemctl start docker

              # Create application directory
              mkdir -p /opt/sentinews
              chown -R ubuntu:ubuntu /opt/sentinews
              usermod -aG docker ubuntu

              echo "[INFO] EC2 bootstrap finished successfully."
              EOF

  tags = {
    Name = "sentinews-${var.environment}-server"
  }
}

# Elastic IP attached to the EC2 instance for static DNS
resource "aws_eip" "app_eip" {
  instance = aws_instance.app_server.id
  domain   = "vpc"

  tags = {
    Name = "sentinews-${var.environment}-eip"
  }
}

# ==============================================================================
# 7. AWS Cost Controls: Budgets ($5, $15, $25) & Cost Anomaly Detection
# ==============================================================================

resource "aws_budgets_budget" "monthly_budget" {
  name              = "sentinews-${var.environment}-monthly-budget"
  budget_type       = "COST"
  limit_amount      = tostring(var.budget_limit_monthly_usd)
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-01-01_00:00"

  # Alert at $5 actual (20%)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 20
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  # Alert at $15 actual (60%)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 60
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  # Alert at $25 actual (100%)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  # Alert if forecast exceeds $25 (100% forecast)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}

resource "aws_ce_anomaly_monitor" "service_monitor" {
  name              = "sentinews-${var.environment}-cost-anomaly-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "email_alert" {
  name      = "sentinews-${var.environment}-anomaly-email-alert"
  frequency = "DAILY"
  monitor_arn_list = [
    aws_ce_anomaly_monitor.service_monitor.arn
  ]

  subscriber {
    type    = "EMAIL"
    address = var.budget_notification_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      values        = ["5"]
      match_options = ["GREATER_THAN_OR_EQUAL"]
    }
  }
}
