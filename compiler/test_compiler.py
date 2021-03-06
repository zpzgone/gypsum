# Copyright 2014-2015, Jay Conrod. All rights reserved.
#
# This file is part of Gypsum. Use of this source code is governed by
# the GPL license that can be found in the LICENSE.txt file.


import unittest
import sys

from lexer import *
from layout import layout
from parser import *
from ast import *
from compile_info import *
from scope_analysis import *
from type_analysis import *
from compiler import *
from ir import *
from ir_types import *
import ir_instructions
from ir_instructions import *
from bytecode import *
from flags import LET
from compile_info import CompileInfo
from builtins import *
from errors import *
from utils_test import TestCaseWithDefinitions

class TestCompiler(TestCaseWithDefinitions):
    def compileFromSource(self, source):
        filename = "(test)"
        rawTokens = lex(filename, source)
        layoutTokens = layout(rawTokens)
        ast = parse(filename, layoutTokens)
        info = CompileInfo(ast)
        analyzeDeclarations(info)
        analyzeInheritance(info)
        analyzeTypes(info)
        convertClosures(info)
        flattenClasses(info)
        compile(info)
        return info.package

    def makePackage(self, input):
        if isinstance(input, Package):
            return input
        elif isinstance(input, AstModule):
            scopeAnalysis(input)
            return compile(input)
        elif type(input) is str:
            return self.compileFromSource(input)

    def makeSimpleFunction(self, name, retTy, blocks,
                           typeParameters=None, parameterTypes=None,
                           variables=None, attribs=frozenset()):
        dummyPackage = Package()
        if variables is None:
            variables = []
        if typeParameters is None:
            typeParameters = []
        if parameterTypes is None:
            parameterTypes = [v.type for v in variables if v.kind is PARAMETER]
        blockList = [BasicBlock(i, insts) for i, insts in enumerate(blocks)]
        function = dummyPackage.addFunction(name, None, retTy, typeParameters, parameterTypes,
                                            variables, blockList, attribs)
        return function

    def checkFunction(self, input, expected):
        package = self.makePackage(input)
        function = package.findFunction(name=expected.name)
        self.assertEquals(expected, function)

    def testEmptyGlobalInit(self):
        self.checkFunction("",
                           self.makeSimpleFunction("$init", UnitType, [[
                               unit(),
                               ret()]]))

    def testDontInitGlobalsWithoutValue(self):
        source = "var o: Object\n" + \
                 "var i: i32"
        self.checkFunction(source,
                           self.makeSimpleFunction("$init", UnitType, [[
                               unit(),
                               ret()]]))

    def testInitGlobal(self):
        self.checkFunction("let x = 42",
                           self.makeSimpleFunction("$init", UnitType, [[
                               i64(42),
                               stg(0),
                               unit(),
                               ret()]]))

    def testBasicFunction(self):
        self.checkFunction("def f = 12",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               ret()]]))

    def testFloatLiterals(self):
        self.checkFunction("def f = 1f32",
                           self.makeSimpleFunction("f", F32Type, [[
                               f32(1.),
                               ret()]]))
        self.checkFunction("def f = 1f64",
                           self.makeSimpleFunction("f", F64Type, [[
                               f64(1.),
                               ret()]]))

    def testEmptyBlock(self):
        self.checkFunction("def f = {}",
                           self.makeSimpleFunction("f", UnitType, [[
                               unit(),
                               ret()]]))

    def testSimpleBlock(self):
        self.checkFunction("def f = { 12; 34; }",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               drop(),
                               i64(34),
                               ret()]]))

    def testBlockWithinBlock(self):
        self.checkFunction("def f = { { 12; }; 34; };",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               drop(),
                               i64(34),
                               ret()]]))

    def testBlockEndingWithStmt(self):
        self.checkFunction("def f = { var x = 12; };",
                           self.makeSimpleFunction("f", UnitType, [[
                               i64(12),
                               stlocal(-1),
                               unit(),
                               ret()]],
                             variables=[self.makeVariable("x", type=I64Type)]))

    def testSimpleVar(self):
        self.checkFunction("def f = { var x = 12; x; }",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               stlocal(-1),
                               ldlocal(-1),
                               ret()]],
                             variables=[self.makeVariable("x", type=I64Type)]))

    def testVarWithSimpleCast(self):
        self.checkFunction("def f = { var x: Object = \"foo\"; x; }",
                           self.makeSimpleFunction("f", getRootClassType(), [[
                               string(0),
                               tyc(BUILTIN_ROOT_CLASS_ID),
                               cast(),
                               stlocal(-1),
                               ldlocal(-1),
                               ret()]],
                            variables=[self.makeVariable("x", type=getRootClassType())]))

    def testVarWithTypeParameterCast(self):
        source = "class Foo[static +T]\n" + \
                 "def f(x: Foo[String]) = { var y: Foo[Object] = x; y; }"
        package = self.compileFromSource(source)
        Foo = package.findClass(name="Foo")
        xType = ClassType(Foo, (getStringType(),))
        yType = ClassType(Foo, (getRootClassType(),))
        expected = self.makeSimpleFunction("f", yType, [[
            ldlocal(0),
            tyc(BUILTIN_ROOT_CLASS_ID),
            tyc(Foo.id),
            cast(),
            stlocal(-1),
            ldlocal(-1),
            ret()]],
          parameterTypes=[xType],
          variables=[self.makeVariable("x", type=xType, kind=PARAMETER, flags=frozenset([LET])),
                     self.makeVariable("y", type=yType)])
        self.assertEquals(expected, package.findFunction(name="f"))

    def testLoadGlobal(self):
        source = "let x = 42\n" + \
                 "def f = x"
        self.checkFunction(source,
                           self.makeSimpleFunction("f", I64Type, [[
                               ldg(0),
                               ret()]]))

    def testStoreGlobal(self):
        source = "var x: i64\n" + \
                 "def f = { x = 12; {}; }"
        self.checkFunction(source,
                           self.makeSimpleFunction("f", UnitType, [[
                               i64(12),
                               stg(0),
                               unit(),
                               ret()]]))

    def testConstMustBeAssigned(self):
        source = "def f = { let x: i64; }"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testSimpleParameter(self):
        self.checkFunction("def f(x: i64) = x",
                           self.makeSimpleFunction("f", I64Type, [[
                               ldlocal(0),
                               ret()]],
                             variables=[self.makeVariable("x", type=I64Type,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testSeveralParameters(self):
        self.checkFunction("def f(var a: i32, var b: boolean, var c: boolean, var d: i32) =\n" + \
                           "  a = 1i32\n" + \
                           "  b = true\n" + \
                           "  c = false\n" + \
                           "  d\n",
                           self.makeSimpleFunction("f", I32Type, [[
                               i32(1),
                               stlocal(0),
                               true(),
                               stlocal(1),
                               false(),
                               stlocal(2),
                               ldlocal(3),
                               ret()]],
                             variables=[
                               self.makeVariable("a", type=I32Type, kind=PARAMETER),
                               self.makeVariable("b", type=BooleanType, kind=PARAMETER),
                               self.makeVariable("c", type=BooleanType, kind=PARAMETER),
                               self.makeVariable("d", type=I32Type, kind=PARAMETER)]))

    def testAssign(self):
        self.checkFunction("def f =\n" + \
                           "  var x = 12\n" + \
                           "  x = 34",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               stlocal(-1),
                               i64(34),
                               dup(),
                               stlocal(-1),
                               ret()]],
                             variables=[self.makeVariable("x", type=I64Type)]))

    def testAssignConst(self):
        source = "def f =\n" + \
                 "  let x = 12\n" + \
                 "  x = 34"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testAssignCapturedConst(self):
        source = "def f(x: i64) =\n" + \
                 "  def g = x = 12"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testAssignParam(self):
        source = "def f(x: i64) = {x = 12; {};}"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testAssignVarParam(self):
        self.checkFunction("def f(var x: i64) = {x = 12; {};}",
                           self.makeSimpleFunction("f", UnitType, [[
                               i64(12),
                               stlocal(0),
                               unit(),
                               ret()]],
                             parameterTypes=[I64Type],
                             variables=[self.makeVariable("x", type=I64Type, kind=PARAMETER)]))

    def testAssignVarWithSimpleCast(self):
        package = self.compileFromSource("def f(s: String, var o: Object) = { o = s; {}; }")
        expected = self.makeSimpleFunction("f", UnitType, [[
            ldlocal(0),
            tyc(BUILTIN_ROOT_CLASS_ID),
            cast(),
            stlocal(1),
            unit(),
            ret()]],
          parameterTypes=[getStringType(), getRootClassType()],
          variables=[self.makeVariable("s", type=getStringType(),
                                       kind=PARAMETER, flags=frozenset([LET])),
                     self.makeVariable("o", type=getRootClassType(), kind=PARAMETER)])
        self.assertEquals(expected, package.findFunction(name="f"))

    def testAssignVarWithTypeParameterCast(self):
        source = "class Foo[static +T]\n" + \
                 "def f(x: Foo[String], var y: Foo[Object]) = { y = x; {}; }"
        package = self.compileFromSource(source)
        Foo = package.findClass(name="Foo")
        xType = ClassType(Foo, (getStringType(),))
        yType = ClassType(Foo, (getRootClassType(),))
        expected = self.makeSimpleFunction("f", UnitType, [[
            ldlocal(0),
            tyc(BUILTIN_ROOT_CLASS_ID),
            tyc(Foo.id),
            cast(),
            stlocal(1),
            unit(),
            ret()]],
          parameterTypes=[xType, yType],
          variables=[self.makeVariable("x", type=xType, kind=PARAMETER, flags=frozenset([LET])),
                     self.makeVariable("y", type=yType, kind=PARAMETER)])
        self.assertEquals(expected, package.findFunction(name="f"))

    def testAssignShortProps(self):
        package = self.compileFromSource("class Foo\n" +
                                         "  var x: boolean\n" +
                                         "  var y: i64\n" +
                                         "def f(foo: Foo) =\n" +
                                         "  foo.x = false\n" +
                                         "  foo.y = 12\n")
        clasTy = ClassType(package.classes[0], ())
        expected = self.makeSimpleFunction("f", I64Type, [[
                       ldlocal(0),
                       false(),
                       swap(),
                       st8(0),
                       ldlocal(0),
                       i64(12),
                       dup(),
                       swap2(),
                       st64(1),
                       ret()]],
                     parameterTypes=[clasTy],
                     variables=[self.makeVariable("foo", type=clasTy,
                                                  kind=PARAMETER, flags=frozenset([LET]))])
        self.assertEquals(expected, package.findFunction(name="f"))

    def testAssignPtrField(self):
        package = self.compileFromSource("class Foo\n" +
                                         "  var self: Foo\n" +
                                         "  def this =\n" +
                                         "    this.self = this")
        clas = package.findClass(name="Foo")
        clasTy = ClassType(clas)
        init = package.findFunction(name="$initializer")
        expected = self.makeSimpleFunction("$constructor", UnitType, [[
                       ldlocal(0),
                       callg(getRootClass().constructors[0].id),
                       drop(),
                       ldlocal(0),
                       callg(init.id),
                       drop(),
                       ldlocal(0),
                       ldlocal(0),
                       swap(),
                       stp(0),
                       unit(),
                       ret()]],
                     parameterTypes=[clasTy],
                     variables=[self.makeVariable("$this", type=clasTy,
                                                  kind=PARAMETER, flags=frozenset([LET]))])
        self.assertEquals(expected, clas.constructors[0])

    def testAssignConstField(self):
        source = "class Foo\n" + \
                 "  let x = 12\n" + \
                 "def f(obj: Foo) = obj.x = 34"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testAssignLocalConstField(self):
        source = "class Foo\n" + \
                 "  let x = 12\n" + \
                 "  def set = x = 34"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testConstFieldMustBeAssigned(self):
        source = "class Foo\n" + \
                 "  let x: i64"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testLoadPtrField(self):
        package = self.compileFromSource("class Foo\n" +
                                         "  var self: Foo\n" +
                                         "def f(foo: Foo) =\n" +
                                         "  foo.self\n")
        clasTy = ClassType(package.classes[0], ())
        expected = self.makeSimpleFunction("f", clasTy, [[
                       ldlocal(0),
                       ldp(0),
                       ret()]],
                     parameterTypes=[clasTy],
                     variables=[self.makeVariable("foo", type=clasTy,
                                                  kind=PARAMETER, flags=frozenset([LET]))])

    def testAccumShortPropForEffect(self):
        package = self.compileFromSource("class Foo\n" +
                                         "  var x: i64\n" +
                                         "def f(foo: Foo) =\n" +
                                         "  foo.x += 12\n" +
                                         "  34")
        clasTy = ClassType(package.classes[0], ())
        expected = self.makeSimpleFunction("f", I64Type, [[
                       ldlocal(0),
                       dup(),
                       ld64(0),
                       i64(12),
                       addi64(),
                       swap(),
                       st64(0),
                       i64(34),
                       ret()]],
                     parameterTypes=[clasTy],
                     variables=[self.makeVariable("foo", type=clasTy,
                                                  kind=PARAMETER, flags=frozenset([LET]))])
        self.assertEquals(expected, package.findFunction(name="f"))

    def testAccumShortPropForValue(self):
        package = self.compileFromSource("class Foo\n" +
                                         "  var x: i64\n" +
                                         "def f(foo: Foo) =\n" +
                                         "  foo.x += 12\n")
        clasTy = ClassType(package.classes[0], ())
        expected = self.makeSimpleFunction("f", I64Type, [[
                       ldlocal(0),
                       dup(),
                       ld64(0),
                       i64(12),
                       addi64(),
                       dup(),
                       swap2(),
                       st64(0),
                       ret()]],
                     parameterTypes=[clasTy],
                     variables=[self.makeVariable("foo", type=clasTy,
                                                  kind=PARAMETER, flags=frozenset([LET]))])
        self.assertEquals(expected, package.findFunction(name="f"))

    def testLoadNonNullableObject(self):
        ty = getRootClassType()
        self.checkFunction("class Foo\n" + \
                           "  def this = { this.x = this; }\n" + \
                           "  var x: Object\n" + \
                           "def f = Foo.x",
                           self.makeSimpleFunction("f", ty, [[
                               allocobj(0),
                               dup(),
                               callg(1),
                               drop(),
                               ldpc(0),
                               ret()]]))

    def testLoadNullableObject(self):
        ty = ClassType(getRootClass(), (), NULLABLE_TYPE_FLAG)
        self.checkFunction("class Foo\n" + \
                           "  def this = {}\n" + \
                           "  var x: Object?\n" + \
                           " def f = Foo.x",
                           self.makeSimpleFunction("f", ty, [[
                               allocobj(0),
                               dup(),
                               callg(1),
                               drop(),
                               ldp(0),
                               ret()]]))

    def testNullableEq(self):
        ty = ClassType(getRootClass(), (), NULLABLE_TYPE_FLAG)
        self.checkFunction("def f(foo: Object?, bar: Object?) = foo === bar",
                           self.makeSimpleFunction("f", BooleanType, [[
                               ldlocal(0),
                               ldlocal(1),
                               eqp(),
                               ret()]],
                             variables=[self.makeVariable("foo", type=ty,
                                                          kind=PARAMETER, flags=frozenset([LET])),
                                        self.makeVariable("bar", type=ty,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testNullableNe(self):
        ty = ClassType(getRootClass(), (), NULLABLE_TYPE_FLAG)
        self.checkFunction("def f(foo: Object?, bar: Object?) = foo !== bar",
                           self.makeSimpleFunction("f", BooleanType, [[
                               ldlocal(0),
                               ldlocal(1),
                               nep(),
                               ret()]],
                             variables=[self.makeVariable("foo", type=ty,
                                                          kind=PARAMETER, flags=frozenset([LET])),
                                        self.makeVariable("bar", type=ty,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testTruncI32(self):
        self.checkFunction("def f(n: i64) = n.to-i32",
                           self.makeSimpleFunction("f", I32Type, [[
                               ldlocal(0),
                               trunci32(),
                               ret()]],
                             variables=[self.makeVariable("n", type=I64Type,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testSext16(self):
        self.checkFunction("def f(n: i8) = n.to-i16",
                           self.makeSimpleFunction("f", I16Type, [[
                               ldlocal(0),
                               sexti16_8(),
                               ret()]],
                             variables=[self.makeVariable("n", type=I8Type,
                                                          kind=PARAMETER,
                                                          flags=frozenset([LET]))]))

    def testF32ToI64(self):
        self.checkFunction("def f(n: f32) = n.to-i64",
                           self.makeSimpleFunction("f", I64Type, [[
                               ldlocal(0),
                               fcvti32(),
                               sexti64_32(),
                               ret()]],
                             variables=[self.makeVariable("n", type=F32Type,
                                                          kind=PARAMETER,
                                                          flags=frozenset([LET]))]))

    def testNeg(self):
        self.checkFunction("def f = - 12",
                           self.makeSimpleFunction("f", I64Type, [[
                             i64(12),
                             negi64(),
                             ret()]]))

    def testAdd(self):
        self.checkFunction("def f = 12 + 34",
                           self.makeSimpleFunction("f", I64Type, [[
                             i64(12),
                             i64(34),
                             addi64(),
                             ret()
                           ]]))

    def testAddAssign(self):
        self.checkFunction("def f = {\n" + \
                           "  var x = 12;\n" + \
                           "  x += 34;\n" + \
                           "};",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               stlocal(-1),
                               ldlocal(-1),
                               i64(34),
                               addi64(),
                               dup(),
                               stlocal(-1),
                               ret()]],
                             variables=[self.makeVariable("x", type=I64Type)]))

    def testAddFloat(self):
        self.checkFunction("def f = 1.2 + 3.4",
                           self.makeSimpleFunction("f", F64Type, [[
                               f64(1.2),
                               f64(3.4),
                               addf64(),
                               ret()]]))

    def testConcatStrings(self):
        stringClass = getStringClass()
        concatMethod = stringClass.getMethod("+")
        concatMethodIndex = stringClass.getMethodIndex(concatMethod)
        self.checkFunction("def f = \"foo\" + \"bar\"",
                           self.makeSimpleFunction("f", getStringType(), [[
                               ir_instructions.string(0),
                               ir_instructions.string(1),
                               callv(2, concatMethodIndex),
                               ret()]]))

    def testCompareStrings(self):
        stringClass = getStringClass()
        eqMethod = stringClass.getMethod("==")
        eqMethodIndex = stringClass.getMethodIndex(eqMethod)
        self.checkFunction("def f = \"foo\" == \"bar\"",
                           self.makeSimpleFunction("f", BooleanType, [[
                               ir_instructions.string(0),
                               ir_instructions.string(1),
                               callv(2, eqMethodIndex),
                               ret()]]))

    def testLessThan(self):
        self.checkFunction("def f = 12 < 34",
                           self.makeSimpleFunction("f", BooleanType, [[
                               i64(12),
                               i64(34),
                               lti64(),
                               ret()]]))

    def testAndExpr(self):
        self.checkFunction("def f = true && false",
                           self.makeSimpleFunction("f", BooleanType, [[
                               true(),
                               dup(),
                               branchif(1, 2),
                             ], [
                               drop(),
                               false(),
                               branch(2),
                             ], [
                               ret()
                             ]]))

    def testOrExpr(self):
        self.checkFunction("def f = true || false",
                           self.makeSimpleFunction("f", BooleanType, [[
                               true(),
                               dup(),
                               branchif(2, 1),
                             ], [
                               drop(),
                               false(),
                               branch(2),
                             ], [
                               ret(),
                             ]]))

    def testIfExpr(self):
        self.checkFunction("def f = if (true) 12 else 34",
                           self.makeSimpleFunction("f", I64Type, [[
                             true(),
                             branchif(1, 2),
                           ], [
                             i64(12),
                             branch(3),
                           ], [
                             i64(34),
                             branch(3),
                           ], [
                             ret(),
                           ]]))

    def testIfStmt(self):
        self.checkFunction("def f = { if (true) 12 else 34; 56; }",
                           self.makeSimpleFunction("f", I64Type, [[
                               true(),
                               branchif(1, 2),
                             ], [
                               i64(12),
                               drop(),
                               branch(3),
                             ], [
                               i64(34),
                               drop(),
                               branch(3),
                             ], [
                               i64(56),
                               ret(),
                             ]]))

    def testIfExprWithoutElse(self):
        self.checkFunction("def f = if (true) 12",
                           self.makeSimpleFunction("f", UnitType, [[
                               true(),
                               branchif(1, 2),
                             ], [
                               i64(12),
                               drop(),
                               branch(2),
                             ], [
                               unit(),
                               ret(),
                             ]]))

    def testIfStmtWithoutElse(self):
        self.checkFunction("def f = { if (true) 12; 34; };",
                           self.makeSimpleFunction("f", I64Type, [[
                               true(),
                               branchif(1, 2),
                             ], [
                               i64(12),
                               drop(),
                               branch(2),
                             ], [
                               i64(34),
                               ret(),
                             ]]))

    def testWhileExpr(self):
        self.checkFunction("def f = while (true) 1",
                           self.makeSimpleFunction("f", UnitType, [[
                             branch(1),
                           ], [
                             true(),
                             branchif(2, 3),
                           ], [
                             i64(1),
                             drop(),
                             branch(1),
                           ], [
                             unit(),
                             ret(),
                           ]]))

    def testWhileBlock(self):
        self.checkFunction("def f = while (true) { 1; };",
                           self.makeSimpleFunction("f", UnitType, [[
                             branch(1),
                           ], [
                             true(),
                             branchif(2, 3),
                           ], [
                             i64(1),
                             drop(),
                             branch(1),
                           ], [
                             unit(),
                             ret(),
                           ]]))

    def testTryWithMatch(self):
        exnClass = getExceptionClass()
        exnTy = ClassType(exnClass)
        typeofMethod = exnClass.getMethod("typeof")
        typeofMethodIndex = exnClass.getMethodIndex(typeofMethod)
        typeClass = getTypeClass()
        typeTy = ClassType(typeClass)
        isSubtypeOfMethod = typeClass.getMethod("is-subtype-of")
        isSubtypeOfMethodIndex = typeClass.getMethodIndex(isSubtypeOfMethod)
        self.checkFunction("def f = try 12 catch\n" +
                           "    case exn => 34",
                           self.makeSimpleFunction("f", I64Type, [[
                               pushtry(1, 2),
                             ], [
                               i64(12),
                               poptry(4),
                             ], [
                               dup(),
                               callv(1, typeofMethodIndex),
                               dup(),
                               allocarri(typeClass.id, 1),
                               dup(),
                               cls(exnClass.id),
                               callg(BUILTIN_TYPE_CTOR_ID),
                               drop(),
                               callv(2, isSubtypeOfMethodIndex),
                               branchif(3, 5),
                             ], [
                               stlocal(-1),
                               drop(),
                               drop(),
                               i64(34),
                               branch(4),
                             ], [
                               ret(),
                             ], [
                               drop(),
                               throw(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=LOCAL, flags=frozenset([LET]))]))

    def testTryWithMatchAndFinally(self):
        exnClass = getExceptionClass()
        exnTy = ClassType(exnClass)
        typeofMethod = exnClass.getMethod("typeof")
        typeofMethodIndex = exnClass.getMethodIndex(typeofMethod)
        typeClass = getTypeClass()
        typeTy = ClassType(typeClass)
        isSubtypeOfMethod = typeClass.getMethod("is-subtype-of")
        isSubtypeOfMethodIndex = typeClass.getMethodIndex(isSubtypeOfMethod)
        self.checkFunction("def f = try 12 catch\n" +
                           "    case exn => 34\n" +
                           "  finally 56",
                           self.makeSimpleFunction("f", I64Type, [[
                               # block 0
                               pushtry(1, 2),
                             ], [
                               # block 1
                               i64(12),
                               poptry(4),
                             ], [
                               # block 2
                               dup(),
                               callv(1, typeofMethodIndex),
                               dup(),
                               allocarri(BUILTIN_TYPE_CLASS_ID, 1),
                               dup(),
                               cls(exnClass.id),
                               callg(BUILTIN_TYPE_CTOR_ID),
                               drop(),
                               callv(2, isSubtypeOfMethodIndex),
                               branchif(3, 5),
                             ], [
                               # block 3
                               stlocal(-1),
                               drop(),
                               drop(),
                               i64(34),
                               branch(4),
                             ], [
                               # block 4
                               null(),
                               branch(6),
                             ], [
                               # block 5
                               drop(),
                               uninitialized(),
                               swap(),
                               branch(6),
                             ], [
                               # block 6
                               i64(56),
                               drop(),
                               dup(),
                               null(),
                               eqp(),
                               branchif(7, 8),
                             ], [
                               # block 7
                               drop(),
                               ret(),
                             ], [
                               # block 8
                               throw(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=LOCAL, flags=frozenset([LET]))]))

    def testAssignVarDefinedInCatch(self):
        source = "def f =\n" + \
                 "  try\n" + \
                 "    {}\n" + \
                 "  catch\n" + \
                 "    case x =>\n" + \
                 "      x = Exception\n" + \
                 "      {}"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testThrow(self):
        exnTy = ClassType(getExceptionClass())
        self.checkFunction("def f(exn: Exception) = throw exn",
                           self.makeSimpleFunction("f", getNothingClassType(), [[
                               ldlocal(0),
                               throw(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testThrowInIfBody(self):
        exnTy = ClassType(getExceptionClass())
        self.checkFunction("def f(exn: Exception) = if (true) throw exn else 12",
                           self.makeSimpleFunction("f", I64Type, [[
                               true(),
                               branchif(1, 2),
                             ], [
                               ldlocal(0),
                               throw(),
                             ], [
                               i64(12),
                               branch(3),
                             ], [
                               ret(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testThrowInIfCondition(self):
        exnTy = ClassType(getExceptionClass())
        self.checkFunction("def f(exn: Exception) = if (throw exn) 12 else 34",
                           self.makeSimpleFunction("f", I64Type, [[
                               ldlocal(0),
                               throw(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testThrowInWhileBody(self):
        exnTy = ClassType(getExceptionClass())
        self.checkFunction("def f(exn: Exception) = while (false) throw exn",
                           self.makeSimpleFunction("f", UnitType, [[
                               branch(1),
                             ], [
                               false(),
                               branchif(2, 3),
                             ], [
                               ldlocal(0),
                               throw(),
                             ], [
                               unit(),
                               ret(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testThrowInTry(self):
        exnClass = getExceptionClass()
        exnTy = ClassType(exnClass)
        typeClass = getTypeClass()
        typeofMethodIndex = next(i for i, m in enumerate(exnClass.methods)
                                 if m.name == "typeof")
        subtypeMethodIndex = next(i for i, m in enumerate(typeClass.methods)
                             if m.name == "is-subtype-of")
        self.checkFunction("def f = try throw Exception catch\n" +
                           "  case exn => 1",
                           self.makeSimpleFunction("f", I64Type, [[
                               pushtry(1, 2),
                             ], [
                               allocobj(BUILTIN_EXCEPTION_CLASS_ID),
                               dup(),
                               callg(BUILTIN_EXCEPTION_CTOR_ID),
                               drop(),
                               throw(),
                             ], [
                               dup(),
                               callv(1, typeofMethodIndex),
                               dup(),
                               allocarri(BUILTIN_TYPE_CLASS_ID, 1),
                               dup(),
                               cls(BUILTIN_EXCEPTION_CLASS_ID),
                               callg(BUILTIN_TYPE_CTOR_ID),
                               drop(),
                               callv(2, subtypeMethodIndex),
                               branchif(3, 5),
                             ], [
                               stlocal(-1),
                               drop(),
                               drop(),
                               i64(1),
                               branch(4),
                             ], [
                               ret(),
                             ], [
                               drop(),
                               throw(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=LOCAL, flags=frozenset([LET]))]))

    def testTryFinallyExpr(self):
        self.checkFunction("def f = try 12 finally 34",
                           self.makeSimpleFunction("f", I64Type, [[
                               pushtry(1, 3),
                             ], [
                               i64(12),
                               poptry(2),
                             ], [
                               null(),
                               branch(4),
                             ], [
                               uninitialized(),
                               swap(),
                               branch(4),
                             ], [
                               i64(34),
                               drop(),
                               dup(),
                               null(),
                               eqp(),
                               branchif(5, 6),
                             ], [
                               drop(),
                               ret(),
                             ], [
                               throw(),
                             ]]))

    def testReturn(self):
        self.checkFunction("def f = return 12",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(12),
                               ret(),
                             ]]))

    def testReturnEmpty(self):
        self.checkFunction("def f = return",
                           self.makeSimpleFunction("f", UnitType, [[
                               unit(),
                               ret(),
                             ]]))

    def testReturnInWhileBody(self):
        self.checkFunction("def f = while (false) return {}",
                           self.makeSimpleFunction("f", UnitType, [[
                               branch(1),
                             ], [
                               false(),
                               branchif(2, 3),
                             ], [
                               unit(),
                               ret(),
                             ], [
                               unit(),
                               ret(),
                             ]]))

    def testReturnBeforeJoin(self):
        self.checkFunction("def f = if (true) return 1 else return 2",
                           self.makeSimpleFunction("f", I64Type, [[
                               true(),
                               branchif(1, 2),
                             ], [
                               i64(1),
                               ret(),
                             ], [
                               i64(2),
                               ret(),
                             ]]))

    def testUnreachableTry(self):
        exnTy = ClassType(getExceptionClass())
        self.checkFunction("def f =\n" +
                           "  return 1\n" +
                           "  try throw Exception catch\n" +
                           "    case exn => 2\n",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(1),
                               ret(),
                             ]],
                             variables=[self.makeVariable("exn", type=exnTy,
                                                          kind=LOCAL, flags=frozenset([LET]))]))

    def testMatchExceptionWithArgument(self):
        source = "class E[static T] <: Exception\n" + \
                 "def f =\n" + \
                 "  try\n" + \
                 "    0\n" + \
                 "  catch\n" + \
                 "    case x: E[String] => 1"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testFactorial(self):
        self.checkFunction("def f(var n: i64): i64 = {\n" +
                           "  var p = 1;\n" +
                           "  while (n > 1) {\n" +
                           "    p = p * n;\n" +
                           "    n = n - 1;\n" +
                           "  };\n" +
                           "  p;\n" +
                           "};",
                           self.makeSimpleFunction("f", I64Type, [[
                               i64(1),
                               stlocal(-1),
                               branch(1),
                             ], [
                               ldlocal(0),
                               i64(1),
                               gti64(),
                               branchif(2, 3),
                             ], [
                               ldlocal(-1),
                               ldlocal(0),
                               muli64(),
                               stlocal(-1),
                               ldlocal(0),
                               i64(1),
                               subi64(),
                               stlocal(0),
                               branch(1),
                             ], [
                               ldlocal(-1),
                               ret(),
                             ]],
                           variables=[
                             self.makeVariable("n", type=I64Type, kind=PARAMETER),
                             self.makeVariable("p", type=I64Type)]))

    def testRequireParameterTypes(self):
        self.assertRaises(TypeException, self.compileFromSource, "def f(x) = 12")

    def testNullaryCall(self):
        source = "def f: i64 = 12\n" + \
                 "def g: i64 = f\n"
        self.checkFunction(source,
                           self.makeSimpleFunction("g", I64Type, [[
                               callg(0),
                               ret(),
                             ]]))

    def testFunctionCall(self):
        source = "def f(x: i64, y: i64): i64 = x\n" + \
                 "def g: i64 = f(12, 34)\n"
        self.checkFunction(source,
                           self.makeSimpleFunction("g", I64Type, [[
                               i64(12),
                               i64(34),
                               callg(0),
                               ret(),
                             ]]))

    def testInitializer(self):
        source = "class Foo\n" + \
                 "  var x: Foo = this\n" + \
                 "  var y: Foo"
        package = self.makePackage(source)
        clas = package.findClass(name="Foo")
        thisType = ClassType(clas)
        self.checkFunction(package,
                           self.makeSimpleFunction("$initializer", UnitType, [[
                               ldlocal(0),
                               ldlocal(0),
                               stp(0),
                               uninitialized(),
                               ldlocal(0),
                               stp(1),
                               unit(),
                               ret()]],
                             parameterTypes=[thisType],
                             variables=[self.makeVariable("$this", type=thisType,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testDefaultCtorCallsInitializer(self):
        source = "class Foo"
        package = self.makePackage(source)
        clas = package.findClass(name="Foo")
        thisType = ClassType(clas)
        ctor = clas.constructors[0]
        init = package.findFunction(name="$initializer")
        self.assertEquals(self.makeSimpleFunction("$constructor", UnitType, [[
                              ldlocal(0),
                              callg(getRootClass().constructors[0].id),
                              drop(),
                              ldlocal(0),
                              callg(init.id),
                              drop(),
                              unit(),
                              ret()]],
                            parameterTypes=[thisType],
                            variables=[self.makeVariable("$this", type=thisType,
                                                         kind=PARAMETER, flags=frozenset([LET]))]),
                          ctor)

    def testPrimaryCtorCallsInitializer(self):
        source = "class Foo(x: i32)\n" + \
                 "  var y = x"
        package = self.makePackage(source)
        clas = package.findClass(name="Foo")
        thisType = ClassType(clas)
        ctor = clas.constructors[0]
        init = clas.initializer
        expected = self.makeSimpleFunction("$constructor", UnitType, [[
            ldlocal(0),
            callg(getRootClass().constructors[0].id),
            drop(),
            ldlocal(1),
            ldlocal(0),
            st32(0),
            ldlocal(0),
            callg(init.id),
            drop(),
            unit(),
            ret()]],
          parameterTypes=[thisType, I32Type],
          variables=[self.makeVariable("$this", type=thisType,
                                       kind=PARAMETER, flags=frozenset([LET]))])
        self.assertEquals(expected, ctor)

    def testSecondaryCtorCallsInitializer(self):
        source = "class Foo\n" + \
                 "  def this(x: i32) =\n" + \
                 "    this.x = x\n" + \
                 "  var x: i32\n" + \
                 "  var y: i32"
        package = self.makePackage(source)
        clas = package.findClass(name="Foo")
        thisType = ClassType(clas)
        ctor = clas.constructors[0]
        init = clas.initializer
        expected = self.makeSimpleFunction("$constructor", UnitType, [[
            ldlocal(0),
            callg(getRootClass().constructors[0].id),
            drop(),
            ldlocal(0),
            callg(init.id),
            drop(),
            ldlocal(0),
            ldlocal(1),
            swap(),
            st32(0),
            unit(),
            ret()]],
          parameterTypes=[thisType, I32Type],
          variables=[self.makeVariable("$this", type=thisType,
                                       kind=PARAMETER, flags=frozenset([LET])),
                     self.makeVariable("x", type=I32Type,
                                       kind=PARAMETER, flags=frozenset([LET]))])
        self.assertEquals(expected, ctor)

    def testNullaryCtor(self):
        source = "class Foo\n" + \
                 "  def this = {}\n" + \
                 "def f = Foo\n"
        package = self.makePackage(source)
        clas = package.findClass(name="Foo")
        objType = ClassType(clas, ())
        self.checkFunction(package,
                           self.makeSimpleFunction("f", objType, [[
                               allocobj(clas.id),
                               dup(),
                               callg(clas.constructors[0].id),
                               drop(),
                               ret(),
                             ]]))

    def testNullaryCtorForEffect(self):
        source = "class Foo\n" + \
                 "  def this = {}\n" + \
                 "def f =\n" + \
                 "  Foo\n" + \
                 "  12"
        package = self.makePackage(source)
        clas = package.findClass(name="Foo")
        objType = ClassType(clas, ())
        self.checkFunction(package,
                           self.makeSimpleFunction("f", I64Type, [[
                               allocobj(clas.id),
                               callg(clas.constructors[0].id),
                               drop(),
                               i64(12),
                               ret()]]))

    def testPrimaryUnaryCtor(self):
        source = "class Foo(x: i32)"
        package = self.compileFromSource(source)
        clas = package.findClass(name="Foo")
        init = package.findFunction(name="$initializer")
        ctor = clas.constructors[0]
        thisType = ClassType(clas)
        self.assertEquals(self.makeSimpleFunction("$constructor", UnitType, [[
                              ldlocal(0),
                              callg(getRootClass().constructors[0].id),
                              drop(),
                              ldlocal(1),
                              ldlocal(0),
                              st32(0),
                              ldlocal(0),
                              callg(init.id),
                              drop(),
                              unit(),
                              ret()]],
                            parameterTypes=[thisType, I32Type],
                            variables=[self.makeVariable("$this", type=thisType,
                                                         kind=PARAMETER, flags=frozenset([LET]))]),
                          ctor)

    def testCtorWithArgs(self):
        source = "class Foo\n" + \
                 "  def this(x: i64, y: i64) = {}\n" + \
                 "def f = Foo(1, 2)\n"
        package = self.compileFromSource(source)
        clas = package.classes[0]
        objType = ClassType(clas, ())
        self.checkFunction(package,
                           self.makeSimpleFunction("f", objType, [[
                               allocobj(clas.id),
                               dup(),
                               i64(1),
                               i64(2),
                               callg(clas.constructors[0].id),
                               drop(),
                               ret(),
                             ]]))

    def testNullaryMethod(self):
        source = "class Foo\n" + \
                 "  def get = 12\n" + \
                 "def f(foo: Foo) = foo.get"
        package = self.compileFromSource(source)
        clas = package.findClass(name="Foo")
        method = package.findFunction(name="get")
        index = clas.getMethodIndex(method)
        objType = ClassType(clas, ())
        self.checkFunction(package,
                           self.makeSimpleFunction("f", I64Type, [[
                               ldlocal(0),
                               callv(1, index),
                               ret()
                             ]],
                             variables=[self.makeVariable("foo", type=objType,
                                                          kind=PARAMETER, flags=frozenset([LET]))]))

    def testCaptureThis(self):
        source = "class Foo\n" + \
                 "  var x: i64\n" + \
                 "  def f =\n" + \
                 "    def g = this.x"
        package = self.compileFromSource(source)
        closureClass = package.findClass(name="$closure")
        closureType = ClassType(closureClass)
        self.checkFunction(package,
                           self.makeSimpleFunction("g", I64Type, [[
                               ldlocal(0),
                               ldpc(0),
                               ldpc(0),
                               ld64(0),
                               ret()
                             ]],
                             variables=[self.makeVariable("$this", type=closureType,
                                                          kind=PARAMETER, flags=frozenset([LET]))],
                             parameterTypes=[closureType]))

    def testCallClosure(self):
        source = "def foo(x: i64) =\n" + \
                 "  def bar = x\n" + \
                 "  bar"
        package = self.compileFromSource(source)
        contextClass = package.findClass(name="$context")
        contextType = ClassType(contextClass)
        closureClass = package.findClass(name="$closure")
        closureType = ClassType(closureClass)
        self.checkFunction(package,
                           self.makeSimpleFunction("foo", I64Type, [[
                               allocobj(contextClass.id),
                               dup(),
                               callg(contextClass.constructors[0].id),
                               drop(),
                               stlocal(-1),
                               ldlocal(0),
                               ldlocal(-1),
                               st64(0),
                               allocobj(closureClass.id),
                               dup(),
                               ldlocal(-1),
                               callg(closureClass.constructors[0].id),
                               drop(),
                               stlocal(-2),
                               ldlocal(-2),
                               callv(1, len(closureClass.methods) - 1),
                               ret()]],
                             variables=[self.makeVariable("$context", type=contextType),
                                        self.makeVariable("bar", type=closureType)],
                             parameterTypes=[I64Type]))

    def testCallAlternateCtor(self):
        source = "class Foo(a: i64)\n" + \
                 "  def this = this(12)"
        package = self.compileFromSource(source)
        clas = package.findClass(name="Foo")
        ty = ClassType(clas)
        expected = self.makeSimpleFunction("$constructor", UnitType, [[
                                               ldlocal(0),
                                               i64(12),
                                               callg(clas.constructors[0].id),
                                               drop(),
                                               unit(),
                                               ret()]],
                                             variables=[self.makeVariable("$this", type=ty,
                                                                          kind=PARAMETER,
                                                                          flags=frozenset([LET]))],
                                             parameterTypes=[ty])
        self.assertEquals(expected, clas.constructors[1])

    def testCallAlternateCtorLater(self):
        source = "class Foo(a: i64)\n" + \
                 "  def this =\n" + \
                 "    var x = 12\n" + \
                 "    this(x)"
        self.assertRaises(SemanticException, self.compileFromSource, source)

    def testCallSuperCtor(self):
        source = "class Foo(a: i64)\n" + \
                 "class Bar <: Foo\n" + \
                 "  def this = super(12)"
        package = self.compileFromSource(source)
        foo = package.findClass(name="Foo")
        bar = package.findClass(name="Bar")
        barTy = ClassType(bar)
        expected = self.makeSimpleFunction("$constructor", UnitType, [[
                                               ldlocal(0),
                                               i64(12),
                                               callg(foo.constructors[0].id),
                                               drop(),
                                               ldlocal(0),
                                               callg(bar.initializer.id),
                                               drop(),
                                               unit(),
                                               ret()]],
                                             variables=[self.makeVariable("$this", type=barTy,
                                                                          kind=PARAMETER, flags=frozenset([LET]))],
                                             parameterTypes=[barTy])
        self.assertEquals(expected, bar.constructors[0])

    def testCallDefaultSuperCtor(self):
        source = "class Foo\n" + \
                 "class Bar <: Foo"
        package = self.compileFromSource(source)
        foo = package.findClass(name="Foo")
        bar = package.findClass(name="Bar")
        barTy = ClassType(bar)
        expected = self.makeSimpleFunction("$constructor", UnitType, [[
                                               ldlocal(0),
                                               callg(foo.constructors[0].id),
                                               drop(),
                                               ldlocal(0),
                                               callg(bar.initializer.id),
                                               drop(),
                                               unit(),
                                               ret()]],
                                             variables=[self.makeVariable("$this", type=barTy,
                                                                          kind=PARAMETER,
                                                                          flags=frozenset([LET]))],
                                             parameterTypes=[barTy])

    def testCallBuiltinFunction(self):
        source = "def f = print(\"foo\")"
        self.checkFunction(source,
                           self.makeSimpleFunction("f", UnitType, [[
                               ir_instructions.string(0),
                               callg(BUILTIN_PRINT_FUNCTION_ID),
                               ret()]]))

    def testCallBuiltinPrimitiveMethod(self):
        source = "def f = {}.to-string"
        self.checkFunction(source,
                           self.makeSimpleFunction("f", getStringType(), [[
                               unit(),
                               callg(BUILTIN_UNIT_TO_STRING_ID),
                               ret()]]))

    def testCallWithStaticClassTypeArgument(self):
        source = "class C\n" + \
                 "def id[static T](x: T) = x\n" + \
                 "def f(o: C) = id[C](o)"
        package = self.compileFromSource(source)
        C = package.findClass(name="C")
        Cty = ClassType(C)
        id = package.findFunction(name="id")
        f = package.findFunction(name="f")
        expected = self.makeSimpleFunction("f", Cty, [[
                       ldlocal(0),
                       tyc(C.id),
                       callg(id.id),
                       ret()]],
                     variables=[self.makeVariable("o", type=Cty,
                                                  kind=PARAMETER, flags=frozenset([LET]))],
                     parameterTypes=[Cty])
        self.assertEquals(expected, f)

    def testCallWithStaticVariableTypeArgument(self):
        source = "def id-outer[static TO](x: TO) = id-inner[TO](x)\n" + \
                 "def id-inner[static TI](x: TI) = x"
        package = self.compileFromSource(source)
        T = package.findTypeParameter(name="TO")
        Tty = VariableType(T)
        inner = package.findFunction(name="id-inner")
        outer = package.findFunction(name="id-outer")
        expected = self.makeSimpleFunction("id-outer", Tty, [[
            ldlocal(0),
            tyv(T.id),
            callg(inner.id),
            ret()]],
          variables=[self.makeVariable("x", type=Tty, kind=PARAMETER, flags=frozenset([LET]))],
          typeParameters=[T],
          parameterTypes=[Tty])
        self.assertEquals(expected, outer)

    def testCallWithExplicitTypeArgument(self):
        source = "def id-outer[static T](x: T) =\n" + \
                 "  def id-inner(x: T) = x\n" + \
                 "  id-inner(x)"
        package = self.compileFromSource(source)
        T = package.findTypeParameter(name="T")
        Tty = VariableType(T)
        idOuter = package.findFunction(name="id-outer")
        idInner = package.findFunction(name="id-inner")
        expected = self.makeSimpleFunction("id-outer", Tty, [[
            ldlocal(0),
            tyv(T.id),
            callg(idInner.id),
            ret()]],
          variables=[self.makeVariable("x", type=Tty, kind=PARAMETER, flags=frozenset([LET]))],
          typeParameters=[T],
          parameterTypes=[Tty])
        self.assertEquals(expected, idOuter)

    def testCallWithImplicitTypeArgument(self):
        source = "def id-outer[static T](x: T) =\n" + \
                 "  def id-inner = x\n" + \
                 "  id-inner"
        package = self.compileFromSource(source)
        T = package.findTypeParameter(name="T")
        Tty = VariableType(T)
        idOuter = package.findFunction(name="id-outer")
        contextClass = package.findClass(name="$context")
        closureClass = package.findClass(name="$closure")
        idInner = package.findFunction(name="id-inner")
        idInnerMethodIndex = closureClass.getMethodIndex(idInner)
        expected = self.makeSimpleFunction("id-outer", Tty, [[
            tyv(T.id),
            allocobj(contextClass.id),
            dup(),
            tyv(T.id),
            callg(contextClass.constructors[0].id),
            drop(),
            stlocal(-1),
            ldlocal(0),
            ldlocal(-1),
            stp(0),
            tyv(T.id),
            allocobj(closureClass.id),
            dup(),
            ldlocal(-1),
            tyv(T.id),
            callg(closureClass.constructors[0].id),
            drop(),
            stlocal(-2),
            ldlocal(-2),
            tyv(T.id),
            callv(1, idInnerMethodIndex),
            ret()]],
          variables=[self.makeVariable("$context", type=ClassType(contextClass)),
                     self.makeVariable("id-inner", type=ClassType(closureClass))],
          typeParameters=[T],
          parameterTypes=[Tty])
        self.assertEquals(expected, idOuter)

    def testCallInheritedMethodWithoutTypeArgument(self):
        source = "class Foo[static T]\n" + \
                 "def f(foo: Foo[Object]) =\n" + \
                 "  foo.to-string\n" + \
                 "  {}"
        package = self.compileFromSource(source)
        f = package.findFunction(name="f")
        Foo = package.findClass(name="Foo")
        FooType = ClassType(Foo, (getRootClassType(),))
        toString = Foo.getMethod("to-string")
        toStringIndex = Foo.getMethodIndex(toString)
        expected = self.makeSimpleFunction("f", UnitType, [[
            ldlocal(0),
            callv(1, toStringIndex),
            drop(),
            unit(),
            ret()]],
          variables=[self.makeVariable("foo", type=FooType,
                                       kind=PARAMETER, flags=frozenset([LET]))],
          parameterTypes=[FooType])
        self.assertEquals(expected, f)

    def testCallOverridenMethodWithTypeArgument(self):
        source = "class Foo[static T]\n" + \
                 "  def to-string = \"Foo\"\n" + \
                 "def f(foo: Foo[Object]) =\n" + \
                 "  foo.to-string\n" + \
                 "  {}"
        package = self.compileFromSource(source)
        f = package.findFunction(name="f")
        Foo = package.findClass(name="Foo")
        FooType = ClassType(Foo, (getRootClassType(),))
        toString = Foo.getMethod("to-string")
        toStringIndex = Foo.getMethodIndex(toString)
        expected = self.makeSimpleFunction("f", UnitType, [[
            ldlocal(0),
            tyc(BUILTIN_ROOT_CLASS_ID),
            callv(1, toStringIndex),
            drop(),
            unit(),
            ret()]],
          variables=[self.makeVariable("foo", type=FooType,
                                       kind=PARAMETER, flags=frozenset([LET]))],
          parameterTypes=[FooType])
        self.assertEquals(expected, f)

    def testConstructorCallInitializerInClassWithStaticTypeArgs(self):
        source = "class C[static T]"
        package = self.compileFromSource(source)
        C = package.findClass(name="C")
        T = package.findTypeParameter(name="T")
        Ctype = ClassType(C, (VariableType(T),))
        expectedCtor = self.makeSimpleFunction("$constructor", UnitType, [[
            ldlocal(0),
            callg(BUILTIN_ROOT_CLASS_CTOR_ID),
            drop(),
            ldlocal(0),
            tyv(T.id),
            callg(C.initializer.id),
            drop(),
            unit(),
            ret()]],
          variables=[self.makeVariable("$this", type=Ctype,
                                       kind=PARAMETER, flags=frozenset([LET]))],
          typeParameters=[T],
          parameterTypes=[Ctype])
        self.assertEquals(expectedCtor, C.constructors[0])

    def testCallClassMethodsWithStaticTypeArgs(self):
        source = "class C\n" + \
                 "class Box[static T](var val: T)\n" + \
                 "  def get = val\n" + \
                 "  def set(val: T) =\n" + \
                 "    this.val = val\n" + \
                 "    {}\n" + \
                 "def f(box: Box[C]) = box.set(box.get)"
        package = self.compileFromSource(source)
        C = package.findClass(name="C")
        Box = package.findClass(name="Box")
        boxType = ClassType(Box, (ClassType(C),))
        get = package.findFunction(name="get")
        set = package.findFunction(name="set")
        f = package.findFunction(name="f")
        expectedF = self.makeSimpleFunction("f", UnitType, [[
            ldlocal(0),
            ldlocal(0),
            tyc(C.id),
            callv(1, Box.getMethodIndex(get)),
            tyc(C.id),
            callv(2, Box.getMethodIndex(set)),
            ret()]],
          variables=[self.makeVariable("box", type=boxType,
                                       kind=PARAMETER, flags=frozenset([LET]))],
          parameterTypes=[boxType])
        self.assertEquals(expectedF, f)

    def testBlockOrdering(self):
        sys.setrecursionlimit(2000)
        source = "def f =\n" + \
                 "  if (true)\n" + \
                 "    if (false)\n" + \
                 "      1\n" + \
                 "    else\n" + \
                 "      2\n" + \
                 "  else\n" + \
                 "    if (false)\n" + \
                 "      3\n" + \
                 "    else\n" + \
                 "      4\n"
        self.checkFunction(source,
                           self.makeSimpleFunction("f", I64Type, [[
                               true(),
                               branchif(1, 5),
                             ], [
                               false(),
                               branchif(2, 3),
                             ], [
                               i64(1),
                               branch(4),
                             ], [
                               i64(2),
                               branch(4),
                             ], [
                               branch(9),
                             ], [
                               false(),
                               branchif(6, 7),
                             ], [
                               i64(3),
                               branch(8),
                             ], [
                               i64(4),
                               branch(8),
                             ], [
                               branch(9),
                             ], [
                               ret()]]))
