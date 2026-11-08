terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}
provider "aws" { region = var.region }
variable "region" {
  type    = string
  default = "ap-southeast-1"
}
variable "domain" {
  type        = string
  description = "Owned DNS name, e.g. haze.example.com. Point its A record to the output IP."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]*[.][a-z]{2,}$", var.domain))
    error_message = "Use a lowercase DNS hostname without a scheme or path."
  }
}
variable "instance_type" {
  type    = string
  default = "t3.medium"
}
variable "config_secret_arn" {
  type        = string
  description = "Existing Secrets Manager secret containing the production dotenv config. See README."
}
resource "random_id" "suffix" { byte_length = 4 }
resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "hazewatch" }
}
data "aws_availability_zones" "available" { state = "available" }
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
}
resource "aws_internet_gateway" "main" { vpc_id = aws_vpc.main.id }
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}
resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}
resource "aws_security_group" "web" {
  name_prefix = "hazewatch-"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_s3_bucket" "source" { bucket = "hazewatch-source-${random_id.suffix.hex}" }
resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
data "archive_file" "source" {
  type        = "zip"
  source_dir  = "${path.module}/.."
  output_path = "${path.module}/../../hazewatch-deploy.zip"
  excludes    = [".venv", "frontend/node_modules", "frontend/dist", "frontend/.angular", "backend/app/__pycache__", "backend/tests/__pycache__", ".pytest_cache", "haze.db", ".env", "infra", ".git"]
}
resource "aws_s3_object" "source" {
  bucket = aws_s3_bucket.source.id
  key    = "source/${data.archive_file.source.output_sha256}.zip"
  source = data.archive_file.source.output_path
  etag   = data.archive_file.source.output_md5
}
resource "aws_iam_role" "host" {
  name_prefix        = "hazewatch-host-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "source" {
  role = aws_iam_role.host.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [aws_s3_object.source.arn] },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [var.config_secret_arn] },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = ["${aws_cloudwatch_log_group.app.arn}:*"] }
  ] })
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "host" { role = aws_iam_role.host.name }
resource "aws_cloudwatch_log_group" "app" {
  name              = "/hazewatch/containers"
  retention_in_days = 14
}
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.host.name
  metadata_options { http_tokens = "required" }
  root_block_device {
    volume_size           = 40
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
    tags                  = { Backup = "hazewatch" }
  }
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    region    = var.region
    source    = "s3://${aws_s3_bucket.source.id}/${aws_s3_object.source.key}"
    secret    = var.config_secret_arn
    domain    = var.domain
    log_group = aws_cloudwatch_log_group.app.name
  })
  tags       = { Name = "hazewatch" }
  depends_on = [aws_route_table_association.public, aws_iam_role_policy.source]
}
resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id
}
resource "aws_cloudwatch_metric_alarm" "host" {
  alarm_name          = "hazewatch-instance-health"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.app.id }
}
resource "aws_iam_role" "backup" {
  name_prefix        = "hazewatch-backup-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "dlm.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}
resource "aws_dlm_lifecycle_policy" "backup" {
  description        = "Daily encrypted volume snapshots"
  execution_role_arn = aws_iam_role.backup.arn
  state              = "ENABLED"
  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Backup = "hazewatch" }
    schedule {
      name = "Daily seven day retention"
      create_rule { interval = 24 }
      retain_rule { count = 7 }
      copy_tags = true
    }
  }
}
output "public_ip" { value = aws_eip.app.public_ip }
output "url" { value = "https://${var.domain}" }
output "instance_id" { value = aws_instance.app.id }
