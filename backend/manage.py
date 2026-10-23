import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
