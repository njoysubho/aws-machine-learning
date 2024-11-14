
data "local_file" "pipeline_definition" {
  filename = "${path.module}/pipeline_definition.json"
}

resource "aws_sagemaker_pipeline" "loan_approval_training_pipeline" {
     pipeline_name = "loan-approval-training-pipeline-${var.enviroment}"
     pipeline_display_name = "loan-approval-training-pipeline-${var.enviroment}"
     pipeline_definition = data.local_file.pipeline_definition.content
}