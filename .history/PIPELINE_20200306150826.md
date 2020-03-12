CI / CD PIPELINE

You can deploy this solution with an automated pipeline by using the below CloudFormation template. It will provision the following resources.


* CodePipeline: A pipeline that will download the source code from Github, build our deployment package using Codebuild and SAM as well as deploy our serverless application into our Account.
* CodePipeline S3 artefact bucket: This is a bucket which CodePipeline will use to store input and output artefacts that are passed between stages. There is also a policy applied with prevents insecure connections.
* CodeBuild Project: We use an Amazon Linux 2 container image running python 3.7 along with the included buildspec.yml file to run SAM build. 
* Roles for Pipeline and Codebuild to allow them access to each other as well as the ability to deploy the resources our project utilises.

You Will need the following information in order to deploy the stack.

Parameters:

   #todo Not sure if once it's part of AWS Labs and public you can deploy directly? 

  GitHubRepoName: The name of the respository that hosts the source code. 
  GitHubRepoBranch: The branch in that repository that you wish to monitor changes from.
  GitHubRepoOwner: The owner of the repository you are pulling the source code from.
  GitHubToken: OAuthToken with access to the Repo. You can find more information about creating a token here: https://github.com/settings/tokens
  AlarmRecipientEmailAddress: An 