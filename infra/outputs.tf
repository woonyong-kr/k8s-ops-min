output "vpc_id" {
  value = module.vpc.vpc_id
}

output "cluster_names" {
  description = "생성된 EKS 클러스터 이름"
  value       = { for k, m in module.eks : k => m.cluster_name }
}

output "cluster_endpoints" {
  value = { for k, m in module.eks : k => m.cluster_endpoint }
}

output "ecr_repository_urls" {
  description = "이미지 push 대상 — aws-up.sh 의 ECR_REPO/CONSOLE_ECR_REPO 와 매칭"
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "kubeconfig_commands" {
  description = "생성 후 kubectl 연결 명령"
  value = [
    for k, m in module.eks :
    "aws eks update-kubeconfig --region ${var.aws_region} --name ${m.cluster_name} --alias ${m.cluster_name}"
  ]
}
