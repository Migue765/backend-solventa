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
        stage('Código') {
            steps { checkout scm }
        }
        stage('Dependencias') {
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
        stage('Formato y lint') {
            steps {
                sh '.venv-jenkins/bin/python -m ruff format --check .'
                sh '.venv-jenkins/bin/python -m ruff check .'
            }
        }
        stage('Tipos') {
            steps {
                sh '''
                    set -eu
                    .venv-jenkins/bin/python -m mypy scripts tests
                    for config in services/*/mypy.ini; do
                        [ -f "$config" ] || continue
                        service="$(dirname "$config")"
                        .venv-jenkins/bin/python -m mypy --config-file "$config" "$service/src" "$service/tests"
                    done
                '''
            }
        }
        stage('Contratos OpenAPI') {
            steps { sh '.venv-jenkins/bin/python scripts/validate_openapi.py' }
        }
        stage('Pruebas y cobertura') {
            steps { sh 'bash scripts/jenkins-tests.sh' }
        }
    }

    post {
        always {
            script {
                if (fileExists('reports/root.xml')) {
                    junit testResults: 'reports/*.xml', allowEmptyResults: false
                }
            }
            archiveArtifacts artifacts: 'reports/*.xml,reports/coverage/**', allowEmptyArchive: true
            deleteDir()
        }
    }
}
