import argparse
import getpass

import attendance


def main():
    parser = argparse.ArgumentParser(description='Create a HuiQian administrator account.')
    subcommands = parser.add_subparsers(dest='command', required=True)
    create = subcommands.add_parser('create-admin')
    create.add_argument('--username', required=True)
    create.add_argument('--super-admin', action='store_true')
    args = parser.parse_args()

    if args.command == 'create-admin':
        first = getpass.getpass('Password: ')
        second = getpass.getpass('Confirm password: ')
        if first != second:
            parser.error('password confirmation does not match')
        attendance.init_db()
        account, error = attendance.create_admin(
            args.username,
            first,
            attendance.ADMIN_TYPE_SUPER if args.super_admin else attendance.ADMIN_TYPE_REGULAR,
        )
        if error:
            parser.error(error)
        print('Administrator created: %s (%s)' % (account['username'], account['account_type']))


if __name__ == '__main__':
    main()
