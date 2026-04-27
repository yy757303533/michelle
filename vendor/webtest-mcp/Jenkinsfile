pipeline {
    agent any

    environment {
        PYTHONPATH = 'src'
    }

    options {
        timeout(time: 30, unit: 'MINUTES')
        timestamps()
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Setup Python') {
            steps {
                sh '''
                    python3 --version || (echo "Python3 not found" && exit 1)
                    pip install -e ".[dev]" -q
                '''
            }
        }

        stage('Test') {
            steps {
                sh '''
                    pytest tests/ -v --tb=short
                '''
            }
        }
    }

    post {
        success {
            echo 'Pipeline succeeded'
        }
        failure {
            echo 'Pipeline failed'
        }
    }
}
