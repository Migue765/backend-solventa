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
            steps {
                deleteDir()
                checkout scm
            }
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
            steps {
                publishChecks name: 'Cobertura', status: 'IN_PROGRESS', conclusion: 'NONE', title: 'Generando cobertura por servicio', summary: 'Scripts y servicios implementados; sin promedio entre módulos.', detailsURL: "https://github.com/Migue765/backend-solventa/pull/${env.CHANGE_ID}/checks"
                sh 'bash scripts/jenkins-tests.sh'
            }
        }
    }

    post {
        always {
            script {
                try {
                    if (fileExists('reports/root.xml')) {
                        junit testResults: 'reports/*.xml', allowEmptyResults: false, skipPublishingChecks: true
                    }
                    def result = sh(returnStatus: true, script: '''
                        set -- --coverage 'Scripts=reports/coverage/root.xml' --tests reports/root.xml
                        for project in services/*/pyproject.toml; do
                            [ -f "$project" ] || continue
                            service="$(basename "$(dirname "$project")")"
                            if find "services/$service/tests" -type f -name 'test_*.py' -print -quit | grep -q .; then
                                set -- "$@" --coverage "$service=reports/coverage/$service.xml" --tests "reports/$service.xml"
                            fi
                        done
                        python3 scripts/coverage_summary.py "$@" --scope 'Scripts y servicios implementados. Cada módulo se mide por separado; sin promedio simple ni dependencias externas.'
                    ''')
                    def summary = readFile(file: 'reports/coverage-summary.md', encoding: 'UTF-8')
                    def revision = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                    summary += "\nRevisión compilada (puede incluir merge con destino): `${revision}`.\n"
                    publishChecks name: 'Cobertura', title: summary.readLines()[0], summary: summary,
                        detailsURL: "https://github.com/Migue765/backend-solventa/pull/${env.CHANGE_ID}/checks",
                        conclusion: result == 0 ? 'SUCCESS' : (currentBuild.currentResult == 'SUCCESS' ? 'FAILURE' : 'NEUTRAL')
                    if (result != 0 && currentBuild.currentResult == 'SUCCESS') {
                        error('Faltan reportes válidos de cobertura o pruebas')
                    }
                } finally {
                    archiveArtifacts artifacts: 'reports/*.xml,reports/coverage-summary.md,reports/coverage/**', allowEmptyArchive: true
                }
            }
        }
        cleanup {
            script {
                try {
                    // Mantener el check antiguo obligatorio hasta migrar las protecciones.
                    publishChecks name: 'CI / Validación (Jenkins)',
                        conclusion: currentBuild.currentResult == 'SUCCESS' ? 'SUCCESS' : 'FAILURE',
                        title: currentBuild.currentResult == 'SUCCESS' ? 'Formato, tipos, contratos y pruebas correctos' : 'Validación incompleta o fallida',
                        summary: 'Validación solicitada con `/test-jenkins`. Cobertura por servicio y pruebas en **Cobertura**. El check anterior se conserva temporalmente por las protecciones.',
                        detailsURL: "https://github.com/Migue765/backend-solventa/pull/${env.CHANGE_ID}/checks"
                } finally {
                    deleteDir()
                }
            }
        }
    }
}
