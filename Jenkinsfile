pipeline {
    agent { label 'backend-ci' }

    options {
        disableConcurrentBuilds()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10', artifactNumToKeepStr: '5'))
        skipDefaultCheckout(true)
        timestamps()
    }

    environment {
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        PIP_CACHE_DIR = "${WORKSPACE}/.pip-cache"
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }
        stage('Install tools and services') {
            steps {
                sh '''
                    set -eu
                    python3.12 -m venv .venv-jenkins
                    .venv-jenkins/bin/python -m pip install -r requirements-dev.txt
                    for project in services/*/pyproject.toml; do
                        [ -f "$project" ] || continue
                        .venv-jenkins/bin/python -m pip install -e "$(dirname "$project")[dev]"
                    done
                '''
            }
        }
        stage('Quality, contracts and tests') {
            steps { sh 'bash scripts/jenkins-quality.sh' }
        }
    }

    post {
        always {
            script {
                if (fileExists('reports/root.xml')) {
                    junit testResults: 'reports/*.xml', allowEmptyResults: false
                }
            }
            archiveArtifacts artifacts: 'reports/*.xml', allowEmptyArchive: true
            deleteDir()
        }
    }
}
