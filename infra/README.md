# Optional AWS deployment

No AWS deployment is required to run or assess this project. This Terraform configuration defines a single-host deployment of the same Compose stack, rather than the more complex ECS/RDS design discussed initially.

## Prerequisites

- Terraform >=1.6, AWS credentials with resource provisioning permissions.
- An owned domain and ability to change its DNS A record.
- A Secrets Manager secret whose SecretString is a **dotenv file**, not JSON.
- A production SMTP service such as SES SMTP with verified sender, appropriate sending access, and TLS credentials.

Create the secret through AWS tooling without committing its content. Example secret content:

```dotenv
POSTGRES_PASSWORD=replace-with-a-long-random-alphanumeric-password
SMTP_HOST=email-smtp.ap-southeast-1.amazonaws.com
SMTP_PORT=587
SMTP_TLS=true
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=alerts@example.com
MAIL_ID_DOMAIN=example.com
DATA_GOV_API_KEY=optional-api-key
```

Use an alphanumeric database password because it is interpolated into a database URL. The instance fetches the secret directly at boot; its contents are not placed in Terraform state or user data. Anyone with host root/SSM access can read the resulting private `.env` file.

## Provision

Run from this directory after confirming no secrets or local data files have been added to the source tree:

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit domain and config_secret_arn
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

Terraform creates a private encrypted source bucket, source archive object, VPC/subnet/internet gateway, security group, IAM instance profile, Ubuntu EC2 host, elastic IP, CloudWatch log group and status alarm, and daily EBS snapshots with seven-day retention. Only HTTP/HTTPS are public. Use SSM Session Manager for host access; SSH is not exposed.

Point the domain's A record to `public_ip`. Caddy obtains a certificate once DNS resolves and ports 80/443 are reachable. Building Angular on the host can take several minutes. Visit the `url` output after cloud-init completes. The host uses live data. Local test inbox and raw API ports remain loopback-only.

The IAM role can read only the generated source object and selected configuration secret. The secret must use the default Secrets Manager key, or add scoped `kms:Decrypt` for your customer-managed KMS key.

## Operations

Use SSM Session Manager, then:

```bash
cd /srv/hazewatch
sudo docker compose -f compose.yaml -f compose.aws.yaml ps
sudo docker compose -f compose.yaml -f compose.aws.yaml logs --tail=100 worker
sudo docker compose -f compose.yaml -f compose.aws.yaml restart api worker
```

Boot diagnostics: `/var/log/cloud-init-output.log`. API and worker container logs stream to `/hazewatch/containers`. The EC2 status alarm has no notification destination until you attach an SNS topic/alarm action.

User data runs only on first boot. Terraform source uploads alone do not deploy application updates to an existing instance. For updates, use SSM to copy the new source object to a staging directory, review changes, back up the database, copy application files without overwriting `.env`, then rerun Compose with `up -d --build`. Secret rotation similarly requires refreshing `.env` and recreating API/worker containers.

Root EBS volume deletion is disabled on instance termination. Data is retained but is not automatically attached to a replacement host; restore or attach it deliberately. This avoids silent data destruction but requires a recovery procedure. Daily snapshots are crash-consistent. Test database restore before production use.

Terraform destroy removes managed resources; retained EBS volumes and snapshots may continue incurring charges. Do not destroy until required backups have been checked. Estimate EC2, EBS, IPv4, S3, snapshot and CloudWatch costs for your account before provisioning.

## Validation status

Terraform formatting and provider initialization passed during authoring. Full provider-schema validation was blocked by the environment prohibiting Unix sockets, so run `terraform validate` and `terraform plan` in your own environment before deployment. No infrastructure was provisioned.

## Scope

This is an assessment-sized deployment, not highly available production infrastructure. No RDS, autoscaling, WAF, managed database migrations, automatic application rollout, or production SLA is implied. Add those when operational requirements warrant them.
