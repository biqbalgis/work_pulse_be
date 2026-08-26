#!/usr/bin/env python
import os
import sys
import warnings
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")
def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'work_pulse_be.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
