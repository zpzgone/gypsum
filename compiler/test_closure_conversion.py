# Copyright 2014-2015, Jay Conrod. All rights reserved.
#
# This file is part of Gypsum. Use of this source code is governed by
# the GPL license that can be found in the LICENSE.txt file.


import unittest

from lexer import *
from layout import layout
from parser import *
from ast import *
from scope_analysis import *
from type_analysis import *
from ir import *
from compile_info import CompileInfo
from ir_types import *
from errors import *
from builtins import *
from flags import LET
from utils_test import TestCaseWithDefinitions


class TestClosureConversion(TestCaseWithDefinitions):
    def analyzeFromSource(self, source):
        filename = "(test)"
        rawTokens = lex(filename, source)
        layoutTokens = layout(rawTokens)
        ast = parse(filename, layoutTokens)
        info = CompileInfo(ast)
        analyzeDeclarations(info)
        analyzeInheritance(info)
        analyzeTypes(info)
        convertClosures(info)
        return info

    def testUseFunctionVarInFunction(self):
        source = "def f =\n" + \
                 "  var x = 12\n" + \
                 "  def g = x\n"
        info = self.analyzeFromSource(source)
        fAst = info.ast.definitions[0]
        fScopeId = info.getScope(info.getDefnInfo(fAst).irDefn).scopeId
        gAst = fAst.body.statements[1]
        gScopeId = info.getScope(info.getDefnInfo(gAst).irDefn).scopeId

        fContextInfo = info.getContextInfo(fScopeId)
        fContextClass = fContextInfo.irContextClass
        self.assertEquals([self.makeField("x", type=I64Type)], fContextClass.fields)
        xDefnInfo = info.getDefnInfo(fAst.body.statements[0].pattern)
        self.assertEquals(self.makeField("x", type=I64Type), xDefnInfo.irDefn)
        self.assertIs(xDefnInfo, info.getUseInfo(gAst.body).defnInfo)
        gClosureInfo = info.getClosureInfo(gScopeId)
        gClosureClass = gClosureInfo.irClosureClass
        self.assertEquals([self.makeField("$context", type=ClassType(fContextClass))],
                          gClosureClass.fields)

        self.assertEquals(self.makeVariable("g", type=ClassType(gClosureClass)),
                          gClosureInfo.irClosureVar)

    def testUseFieldInMethod(self):
        source = "class C\n" + \
                 "  var x = 12\n" + \
                 "  def f = x"
        info = self.analyzeFromSource(source)
        cAst = info.ast.definitions[0]
        C = info.package.findClass(name="C")
        cScopeId = info.getScope(C).scopeId
        cContextInfo = info.getContextInfo(cScopeId)
        self.assertIs(C, cContextInfo.irContextClass)
        xDefnInfo = info.getDefnInfo(cAst.members[0].pattern)
        xUseInfo = info.getUseInfo(cAst.members[1].body)
        self.assertIs(xDefnInfo, xUseInfo.defnInfo)

    def testUseMethodInMethod(self):
        source = "class C\n" + \
                 "  def f = 12\n" + \
                 "  def g = f\n"
        info = self.analyzeFromSource(source)
        cAst = info.ast.definitions[0]
        C = info.package.findClass(name="C")
        cScopeId = info.getScope(C).scopeId
        cContextInfo = info.getContextInfo(cScopeId)
        self.assertIs(C, cContextInfo.irContextClass)
        fDefnInfo = info.getDefnInfo(cAst.members[0])
        fUseInfo = info.getUseInfo(cAst.members[1].body)
        self.assertIs(fDefnInfo, fUseInfo.defnInfo)

    def testCaptureThis(self):
        source = "class C\n" + \
                 "  def f =\n" + \
                 "    def g = this"
        info = self.analyzeFromSource(source)
        cAst = info.ast.definitions[0]
        C = info.package.findClass(name="C")
        CType = ClassType(C)
        f = info.package.findFunction(name="f")
        fScopeId = info.getScope(f).scopeId
        fContextInfo = info.getContextInfo(fScopeId)
        fContextClass = info.package.findClass(name="$context")
        self.assertIs(fContextClass, fContextInfo.irContextClass)
        self.assertEquals(1, len(fContextClass.constructors))
        self.assertEquals([self.makeField("$this", type=CType, flags=frozenset([LET]))],
                          fContextClass.fields)
        g = info.package.findFunction(name="g")
        gScopeId = info.getScope(g).scopeId
        gClosureInfo = info.getClosureInfo(gScopeId)
        gClosureClass = info.package.findClass(name="$closure")
        self.assertIs(gClosureClass, gClosureInfo.irClosureClass)
        self.assertEquals({fScopeId: gClosureClass.fields[0]}, gClosureInfo.irClosureContexts)
        self.assertTrue(gClosureInfo.irClosureVar in f.variables)
        self.assertEquals(1, len(gClosureClass.constructors))
        self.assertEquals([ClassType(gClosureClass), ClassType(fContextClass)],
                          gClosureClass.constructors[0].parameterTypes)
        self.assertEquals([self.makeField("$context", type=ClassType(fContextClass))],
                          gClosureClass.fields)
