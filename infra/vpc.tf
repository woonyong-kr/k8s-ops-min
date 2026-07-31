# 공용 VPC — 3개 클러스터가 같은 VPC 의 프라이빗 서브넷을 사용한다.
# (agent 는 outbound 만 필요한 pull 모델이라 클러스터 간 인바운드 개방이 필요 없다)
data "aws_availability_zones" "available" {
  state = "available"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.16"

  name = "${var.project_slug}-vpc"
  cidr = var.vpc_cidr

  azs             = slice(data.aws_availability_zones.available.names, 0, 3)
  private_subnets = [for i in range(3) : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i in range(3) : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway   = true
  single_nat_gateway   = true # 실습/데모 비용 절감 — 프로덕션은 AZ 당 1개 권장
  enable_dns_support   = true
  enable_dns_hostnames = true

  # EKS 로드밸런서 배치용 서브넷 태그
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}
