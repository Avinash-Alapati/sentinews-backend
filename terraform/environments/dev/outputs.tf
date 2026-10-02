output "ec2_public_ip" {
  description = "Elastic IP address of the EC2 application server"
  value       = aws_eip.app_eip.public_ip
}

output "ec2_instance_id" {
  description = "EC2 Instance ID for SSM Session Manager connections"
  value       = aws_instance.app_server.id
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = aws_ecr_repository.app.repository_url
}

output "s3_backup_bucket_name" {
  description = "Name of the S3 database backup bucket"
  value       = aws_s3_bucket.backups.id
}

output "ssm_connect_command" {
  description = "AWS CLI command to connect to instance via SSM Session Manager (no SSH key needed)"
  value       = "aws ssm start-session --target ${aws_instance.app_server.id} --region ${var.aws_region}"
}
