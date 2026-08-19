#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

python manage.py tailwind install

exec python manage.py tailwind start
