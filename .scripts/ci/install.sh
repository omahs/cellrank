#!/bin/bash

set -e

if [[ "$TRAVIS_OS_NAME" == "osx" ]]; then
    pip3 install -e".[test]"
elif [[ "$TRAVIS_OS_NAME" == "linux" ]]; then
    if [[ "$KRYLOV_EXTRA" == "true" ]]; then
        pip install -e.[krylov,test]
        python -c "import slepc; import petsc;"
        echo "Succesfully installed SLEPc and PETSc"
    else
        pip install -e.[test]
    fi
fi

python-vendorize
