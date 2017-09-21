#!/usr/bin/env python3
# -*- coding: utf-8 -*-

class UdfTestCase():

    @staticmethod
    def setup():
        print ("setup")

    @staticmethod
    def run():
        print ("run")

    @staticmethod
    def teardown():
        print ("teardown")
