#!/usr/bin/env bash

python3 manage.py makemessages --keep-pot --no-wrap --no-obsolete --ignore node_modules -v 3
python3 manage.py compilemessages
