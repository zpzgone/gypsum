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
from ir_types import *
from compile_info import *
from location import NoLoc
from flags import LET
from errors import *
from utils_test import TestCaseWithDefinitions


class TestUseAnalysis(TestCaseWithDefinitions):
    def parseFromSource(self, source):
        filename = "(test)"
        rawTokens = lex(filename, source)
        layoutTokens = layout(rawTokens)
        ast = parse(filename, layoutTokens)
        return ast

    def analyzeFromSource(self, source):
        ast = self.parseFromSource(source)
        info = CompileInfo(ast)
        analyzeDeclarations(info)
        analyzeInheritance(info)
        return info

    def analyzeFromSourceWithTypes(self, source):
        info = self.analyzeFromSource(source)
        analyzeTypes(info)
        return info

    def testUndefinedReference(self):
        info = self.analyzeFromSource("var x = y")
        self.assertRaises(ScopeException,
                          info.getScope(GLOBAL_SCOPE_ID).lookup, "y", NoLoc)

    def testUseVarBeforeDefinition(self):
        info = self.analyzeFromSource("def f = { var x = y; var y = 12; }")
        scope = info.getScope(info.ast.definitions[0])
        self.assertRaises(ScopeException, scope.lookup, "y", NoLoc)

    def testUseFunctionBeforeDefinition(self):
        info = self.analyzeFromSource("def f = g; def g = 12;")
        gDefnInfo = info.getDefnInfo(info.ast.definitions[1])
        gNameInfo = info.getScope(info.ast.definitions[0]).lookup("g", NoLoc)
        self.assertIs(gDefnInfo, gNameInfo.getDefnInfo())

    def testUseCapturedVarBeforeDefinition(self):
        info = self.analyzeFromSource("def f =\n" + \
                                      "  def g = i = 1\n" + \
                                      "  var i: i64 = 0\n")
        statements = info.ast.definitions[0].body.statements
        iDefnInfo = info.getDefnInfo(statements[1].pattern)
        gScope = info.getScope(statements[0])
        iNameInfo = gScope.lookup("i", NoLoc)
        self.assertIs(iDefnInfo, iNameInfo.getDefnInfo())

    def testUseClassBeforeDefinition(self):
        info = self.analyzeFromSource("def f = C; class C;")
        cDefnInfo = info.getDefnInfo(info.ast.definitions[1])
        cNameInfo = info.getScope(GLOBAL_SCOPE_ID).lookup("C", NoLoc)
        self.assertIs(cDefnInfo, cNameInfo.getDefnInfo())

    def testUseInLocalScope(self):
        info = self.analyzeFromSource("def f(x: i64) = { { x; }; };")
        fScope = info.getScope(info.ast.definitions[0])
        fScope.define("x")
        xDefnInfo = info.getDefnInfo(info.ast.definitions[0].parameters[0].pattern)
        localScope = info.getScope(info.ast.definitions[0].body.statements[0])
        xNameInfo = localScope.lookup("x", NoLoc)
        self.assertIs(xDefnInfo, xNameInfo.getDefnInfo())

    def testUseThisInInitializer(self):
        info = self.analyzeFromSource("class Foo { var x = this; };")
        classScope = info.getScope(info.ast.definitions[0])
        thisNameInfo = classScope.lookup("this", NoLoc)
        self.assertEquals(DefnInfo(self.makeVariable("$this", kind=PARAMETER,
                                                     flags=frozenset([LET])),
                                   classScope.scopeId, classScope.scopeId, NOT_HERITABLE),
                          thisNameInfo.getDefnInfo())

    def testUsePrivateOuter(self):
        source = "class C\n" + \
                 "  private def f = {}\n" + \
                 "def g(o: C) = o.f"
        self.assertRaises(ScopeException, self.analyzeFromSourceWithTypes, source)

    def testUsePrivateSubclass(self):
        source = "class A\n" + \
                 "  private def f = {}\n" + \
                 "class B <: A\n" + \
                 "  def g = f"
        self.assertRaises(ScopeException, self.analyzeFromSourceWithTypes, source)

    def testUsePrivateSibling(self):
        source = "class C\n" + \
                 "  private def f = {}\n" + \
                 "  def g = f\n"
        info = self.analyzeFromSourceWithTypes(source)
        use = info.getUseInfo(info.ast.definitions[0].members[1].body)
        self.assertEquals(info.getScope(info.ast.definitions[0].members[1]).scopeId,
                          use.useScopeId)
        self.assertEquals(USE_AS_VALUE, use.kind)

    def testUsePrivateChild(self):
        source = "class C\n" + \
                 "  private def f = {}\n" + \
                 "  def g =\n" + \
                 "    def h = f"
        info = self.analyzeFromSourceWithTypes(source)
        useScopeAst = info.ast.definitions[0].members[1].body.statements[0]
        use = info.getUseInfo(useScopeAst.body)
        self.assertEquals(info.getScope(useScopeAst).scopeId, use.useScopeId)
        self.assertEquals(USE_AS_VALUE, use.kind)

    def testUsePrivateInnerWithReceiver(self):
        source = "class C\n" + \
                 "  private var x: i64\n" + \
                 "  def f(other: C) = other.x"
        info = self.analyzeFromSourceWithTypes(source)
        use = info.getUseInfo(info.ast.definitions[0].members[1].body)
        self.assertEquals(info.getScope(info.ast.definitions[0].members[1]).scopeId,
                          use.useScopeId)
        self.assertEquals(USE_AS_PROPERTY, use.kind)

    def testUseProtectedOuter(self):
        source = "class C\n" + \
                 "  protected def f = {}\n" + \
                 "def g(o: C) = o.f"
        self.assertRaises(ScopeException, self.analyzeFromSourceWithTypes, source)

    def testUseProtectedSibling(self):
        source = "class C\n" + \
                 "  protected def f = {}\n" + \
                 "  def g = f\n"
        info = self.analyzeFromSourceWithTypes(source)
        useScopeAst = info.ast.definitions[0].members[1]
        use = info.getUseInfo(useScopeAst.body)
        self.assertEquals(info.getScope(useScopeAst).scopeId, use.useScopeId)
        self.assertEquals(USE_AS_VALUE, use.kind)

    def testUseProtectedInherited(self):
        source = "class A\n" + \
                 "  protected def f = {}\n" + \
                 "class B <: A\n" + \
                 "  def g = f\n"
        info = self.analyzeFromSourceWithTypes(source)
        useScopeAst = info.ast.definitions[1].members[0]
        use = info.getUseInfo(useScopeAst.body)
        self.assertEquals(info.getScope(useScopeAst).scopeId, use.useScopeId)
        self.assertEquals(USE_AS_VALUE, use.kind)

    def testCallAbstractClassCtor(self):
        source = "abstract class A\n" + \
                 "def f = A"
        self.assertRaises(ScopeException, self.analyzeFromSourceWithTypes, source)

    # Regression tests
    def testUseTypeParameterInLaterPrimaryCtor(self):
        source = "class Foo\n" + \
                 "  def make-bar = Bar[Foo](this)\n" + \
                 "class Bar[static +T](value: T)"
        info = self.analyzeFromSourceWithTypes(source)
        T = info.package.findTypeParameter(name="T")
        use = info.getUseInfo(info.ast.definitions[1].constructor.parameters[0].pattern.ty)
        self.assertIs(T, use.defnInfo.irDefn)

    def testUseTypeParameterInLaterPrimaryCtorField(self):
        source = "class Foo\n" + \
                 "  def get(bar: Bar[String]) = bar.value\n" + \
                 "class Bar[static +T](value: T)"
        info = self.analyzeFromSourceWithTypes(source)
        self.assertEquals(getStringType(),
                          info.getType(info.ast.definitions[0].members[0].body))

    def testUseTypeParameterInLaterField(self):
        source = "class Foo\n" + \
                 "  def get(bar: Bar[String]) = bar.value\n" + \
                 "class Bar[static T]\n" + \
                 "  var value: T"
        info = self.analyzeFromSourceWithTypes(source)
        self.assertEquals(getStringType(),
                          info.getType(info.ast.definitions[0].members[0].body))

    def testUseTypeParameterInBound(self):
        source = "class A[static T]\n" + \
                 "def f[static S <: A[S]] = {}"
        info = self.analyzeFromSourceWithTypes(source)
        S = info.package.findTypeParameter(name="S")
        use = info.getUseInfo(info.ast.definitions[1].typeParameters[0].upperBound.typeArguments[0])
        self.assertIs(S, use.defnInfo.irDefn)
