#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from argparse import ArgumentParser

class TestCase():

    @staticmethod
    def _load_class(name):
        mod = __import__('scripts.' + name, fromlist = [name])
        return getattr(mod, name)

    @staticmethod
    def setup(args):
        klass = TestCase._load_class(args.clazz)
        klass.setup()

    @staticmethod
    def run(args):
        klass = TestCase._load_class(args.clazz)
        klass.run()

    @staticmethod
    def teardown(args):
        klass = TestCase._load_class(args.clazz)
        klass.teardown()

def main():
    parser = ArgumentParser()
    parser.add_argument('clazz')
    subparsers = parser.add_subparsers(title='subcommands')

    setup_parser = subparsers.add_parser('setup')
    setup_parser.set_defaults(func=TestCase.setup)
    run_parser = subparsers.add_parser('run')
    run_parser.set_defaults(func=TestCase.run)
    teardown_parser = subparsers.add_parser('teardown')
    teardown_parser.set_defaults(func=TestCase.teardown)

    params = parser.parse_args()

    if hasattr(params, 'func'):
        success = params.func(params)
        return 0 if success else 1
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
