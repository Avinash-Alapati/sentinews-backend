variable "environment" {
  type        = string
  default     = "dev"
  description = "Environment name (dev/staging)"
}

variable "aws_region" {
  type        = string
  default     = "ap-south-1"
  description = "AWS Mumbai region"
}

variable "instance_type" {
  type        = string
  default     = "t4g.small"
  description = "EC2 instance type (t4g.small for Graviton3 2GB RAM @ ~$12/mo, or t3.small for x86)"
}

variable "root_volume_size_gb" {
  type        = number
  default     = 20
  description = "Size of EBS gp3 root volume in GB"
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "Optional custom domain name for Caddy automatic TLS (e.g. api.sentinews.com)"
}

variable "acme_email" {
  type        = string
  default     = "admin@sentinews.com"
  description = "Email address for Let's Encrypt / ZeroSSL TLS certificates in Caddy"
}

variable "budget_limit_monthly_usd" {
  type        = number
  default     = 25
  description = "Monthly AWS budget limit in USD"
}

variable "budget_notification_email" {
  type        = string
  default     = "admin@sentinews.com"
  description = "Notification email for AWS budget alerts ($5, $15, $25) and Cost Anomaly Detection"
}
