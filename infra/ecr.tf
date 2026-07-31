# ECR 이미지 저장소 — service(백엔드 전 서비스 공용 이미지) + console(프론트).
resource "aws_ecr_repository" "this" {
  for_each = toset(var.ecr_repositories)

  name                 = "${var.project_slug}-${each.value}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # 실습용 — destroy 시 이미지까지 정리(프로덕션은 false)

  image_scanning_configuration {
    scan_on_push = true
  }
}

# 오래된 이미지 자동 정리 — 최근 20개 태그만 유지(스토리지 비용 통제).
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}
