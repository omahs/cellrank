#!/bin/bash

set -e

if [[ "$TRAVIS_OS_NAME" == "osx" ]]; then
    pip3 install -U pip
elif [[ "$TRAVIS_OS_NAME" == "linux" ]]; then
    pip install pytest-cov
    if [[ "$USE_SLEPC" == "true" ]]; then
        echo "Installing dependencies"
        sudo apt-get update -y
        sudo apt-get install gcc gfortran libopenmpi-dev libblas-dev liblapack-dev -y

        if [[ "$KRYLOV_EXTRA" == "false" ]]; then
            pip_cmd=$(which pip)
            echo "Installing SLEPc and PETSc"

            sudo $pip_cmd install mpi4py

            sudo $pip_cmd install petsc
            sudo $pip_cmd install petsc4py

            sudo $pip_cmd install slepc
            sudo $pip_cmd install slepc4py

            python -c "import slepc; import petsc;"
            echo "Succesfully installed SLEPc and PETSc"
        fi
    fi
fi
