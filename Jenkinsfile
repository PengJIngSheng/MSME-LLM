pipeline {
    agent any

    environment {
        DEPLOY_HOST = '103.209.158.127'
        DEPLOY_USER = 'ubuntu'
        DEPLOY_DIR  = '/home/ubuntu/MSME-LLM'
        SERVICE_NAME = 'msme-llm'          // systemd service name (see Step 4)
        SSH_CRED_ID  = 'deploy-server-ssh' // Jenkins credential ID
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Deploy') {
            steps {
                sshagent(credentials: [SSH_CRED_ID]) {
                    sh """
                        ssh -o StrictHostKeyChecking=no ${DEPLOY_USER}@${DEPLOY_HOST} '
                            set -e
                            cd ${DEPLOY_DIR}

                            echo "==> Pulling latest code..."
                            git pull origin main

                            echo "==> Installing/updating dependencies..."
                            pip install -r requirements.txt -q

                            echo "==> Restarting service..."
                            sudo systemctl restart ${SERVICE_NAME}

                            echo "==> Deploy done."
                            systemctl status ${SERVICE_NAME} --no-pager
                        '
                    """
                }
            }
        }
    }

    post {
        success {
            echo "Deployment succeeded."
        }
        failure {
            echo "Deployment failed — check SSH logs above."
        }
    }
}
