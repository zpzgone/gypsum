// Copyright 2014 Jay Conrod. All rights reserved.

// This file is part of CodeSwitch. Use of this source code is governed by
// the 3-clause BSD license that can be found in the LICENSE.txt file.


#include "interpreter.h"

#include <algorithm>
#include <iostream>
#include <sstream>
#include <string>
#include <type_traits>
#include "array.h"
#include "block-inl.h"
#include "builtins.h"
#include "bytecode.h"
#include "function-inl.h"
#include "gc.h"
#include "handle-inl.h"
#include "object.h"
#include "package-inl.h"
#include "stack-inl.h"

using namespace std;

namespace codeswitch {
namespace internal {

Interpreter::Interpreter(VM* vm, Handle<Stack> stack)
    : vm_(vm),
      stack_(stack),
      pcOffset_(kDonePcOffset) { }


// Stack methods need to go before instantiation so they can be specialized.
template <typename T>
void Interpreter::push(T value) {
  typename std::make_unsigned<T>::type unsignedValue = value;
  stack_->push(static_cast<u64>(unsignedValue));
}


template <>
void Interpreter::push<bool>(bool value) {
  stack_->push(static_cast<u64>(value));
}


template <>
void Interpreter::push<f32>(f32 value) {
  auto bits = static_cast<u64>(*reinterpret_cast<u32*>(&value));
  stack_->push(bits);
}


template <>
void Interpreter::push<f64>(f64 value) {
  stack_->push(value);
}


template <>
void Interpreter::push<void*>(void* value) {
  static_assert(sizeof(void*) == sizeof(i64), "Specialization requires 64-bit");
  stack_->push(value);
}


template <>
void Interpreter::push<Block*>(Block* value) {
  static_assert(sizeof(Block*) == sizeof(i64), "Specialization requires 64-bit");
  stack_->push(value);
}


template <typename T>
T Interpreter::pop() {
  return static_cast<T>(stack_->pop<u64>());
}


template <>
f32 Interpreter::pop<f32>() {
  auto bits = static_cast<u32>(stack_->pop<u64>());
  return *reinterpret_cast<f32*>(&bits);
}


template <>
f64 Interpreter::pop<f64>() {
  return stack_->pop<f64>();
}


template <>
Block* Interpreter::pop<Block*>() {
  static_assert(sizeof(Block*) == sizeof(i64), "Specialization requires 64-bit");
  return stack_->pop<Block*>();
}


#define ALLOCATE_GC_RETRY(type, var, expr)                            \
type var = expr;                                                      \
if (var == nullptr) {                                                 \
  collectGarbage();                                                   \
  var = expr;                                                         \
  ASSERT(var != nullptr);                                             \
}                                                                     \


#define CHECK_NON_NULL(block)                                         \
  if ((block) == nullptr) {                                           \
    throwBuiltinException(BUILTIN_NULL_POINTER_EXCEPTION_CLASS_ID);   \
    break;                                                            \
  }                                                                   \


#define CHECK_INITIALIZED(block)                                      \
  if ((block) == nullptr) {                                           \
    throwBuiltinException(BUILTIN_UNINITIALIZED_EXCEPTION_CLASS_ID);  \
    break;                                                            \
  }                                                                   \


i64 Interpreter::call(Handle<Function> callee) {
  // Set up initial stack frame.
  ASSERT(pcOffset_ == kDonePcOffset);
  enter(callee);

  // Interpreter loop.
  i64 result;
  while (pcOffset_ != kDonePcOffset) {
    u8 bc = function_->instructionsStart()[pcOffset_++];
    auto opc = static_cast<Opcode>(bc);

    switch (opc) {
      case NOP:
        break;

      case RET: {
        auto value = pop<i64>();
        leave();
        if (pcOffset_ == kDonePcOffset) {
          result = value;
        } else {
          push(value);
        }
        break;
      }

      case BRANCH: {
        i64 blockIndex = readVbn();
        pcOffset_ = function_->blockOffset(static_cast<word_t>(blockIndex));
        break;
      }

      case BRANCHIF: {
        auto trueBlockIndex = static_cast<word_t>(readVbn());
        auto falseBlockIndex = static_cast<word_t>(readVbn());
        auto condition = pop<bool>();
        pcOffset_ = function_->blockOffset(condition ? trueBlockIndex : falseBlockIndex);
        break;
      }

      case PUSHTRY: {
        auto tryBlockIndex = static_cast<word_t>(readVbn());
        auto catchBlockIndex = static_cast<word_t>(readVbn());
        pcOffset_ = function_->blockOffset(tryBlockIndex);
        Handler handler = { stack_->framePointerOffset(),
                            stack_->stackPointerOffset(),
                            function_->blockOffset(catchBlockIndex) };
        handlers_.push_back(handler);
        break;
      }

      case POPTRY: {
        auto doneBlockIndex = static_cast<word_t>(readVbn());
        pcOffset_ = function_->blockOffset(doneBlockIndex);
        handlers_.pop_back();
        break;
      }

      case THROW: {
        auto exception = pop<Block*>();
        doThrow(exception);
        break;
      }

      case DROP:
        pop<i64>();
        break;

      case DUP: {
        i64 value = mem<i64>(stack_->sp());
        push(value);
        break;
      }

      case DUPI: {
        auto index = readVbn();
        auto value = mem<i64>(stack_->sp(), 0, index);
        push(value);
        break;
      }

      case SWAP: {
        i64& top = mem<i64>(stack_->sp());
        i64& other = mem<i64>(stack_->sp(), 0, 1);
        swap(top, other);
        break;
      }

      case SWAP2: {
        i64& top = mem<i64>(stack_->sp());
        i64& other = mem<i64>(stack_->sp(), 0, 2);
        swap(top, other);
        break;
      }

      case UNIT:
      case FALSE:
      case NUL:
        push(0);
        break;

      case TRUE:
        push(1);
        break;

      case UNINITIALIZED:
        push(kUninitialized);
        break;

      case I8: {
        auto value = static_cast<i8>(readVbn());
        push(value);
        break;
      }

      case I16: {
        auto value = static_cast<i16>(readVbn());
        push(value);
        break;
      }

      case I32: {
        auto value = static_cast<i32>(readVbn());
        push(value);
        break;
      }

      case I64: {
        auto value = readVbn();
        push(value);
        break;
      }

      case F32: {
        auto value = mem<u32>(function_->instructionsStart(), pcOffset_);
        pcOffset_ += 4;
        push(value);
        break;
      }

      case F64: {
        auto value = mem<u64>(function_->instructionsStart(), pcOffset_);
        pcOffset_ += 8;
        push(value);
        break;
      }

      case STRING: {
        auto index = readVbn();
        String* string = String::cast(function_->package()->getString(index));
        push<Block*>(string);
        break;
      }

      case LDLOCAL: {
        auto index = readVbn();
        auto addr = localAddressFromIndex(index);
        auto value = mem<i64>(addr);
        push(value);
        break;
      }

      case STLOCAL: {
        auto index = readVbn();
        auto addr = localAddressFromIndex(index);
        auto value = pop<i64>();
        mem<i64>(addr) = value;
        break;
      }

      case LD8: loadObject<i8>(); break;
      case LD16: loadObject<i16>(); break;
      case LD32: loadObject<i32>(); break;
      case LD64: loadObject<i64>(); break;
      case LDP: loadObject<i64>(); break;

      case LDPC: {
        auto index = readVbn();
        auto block = pop<Block*>();
        CHECK_NON_NULL(block);
        auto offset = block->meta()->clas()->findFieldOffset(index);
        auto value = mem<Block*>(block, offset);
        CHECK_INITIALIZED(value);
        push(value);
        break;
      }

      case ST8: storeObject<i8>(); break;
      case ST16: storeObject<i16>(); break;
      case ST32: storeObject<i32>(); break;
      case ST64: storeObject<i64>(); break;

      case STP: {
        auto index = readVbn();
        auto block = pop<Block*>();
        auto value = pop<Block*>();
        CHECK_NON_NULL(block);
        auto offset = block->meta()->clas()->findFieldOffset(index);
        auto addr = &mem<Block*>(block, offset);
        *addr = value;
        vm_->heap()->recordWrite(addr, value);
        break;
      }

      case ALLOCOBJ: {
        auto classId = readVbn();
        auto meta = getMetaForClassId(classId);
        ALLOCATE_GC_RETRY(Object*, obj, Object::tryAllocate(vm_->heap(), *meta))
        push<Block*>(obj);
        break;
      }

      case ALLOCARRI: {
        auto classId = readVbn();
        auto length = readVbn();
        auto meta = getMetaForClassId(classId);
        ALLOCATE_GC_RETRY(Object*, obj, Object::tryAllocateArray(vm_->heap(), *meta, length))
        push<Block*>(obj);
        break;
      }

      case CLS: {
        auto classId = readVbn();
        Class* clas = isBuiltinId(classId)
            ? vm_->roots()->getBuiltinClass(classId)
            : function_->package()->getClass(classId);
        push<Block*>(clas);
        break;
      }

      case TYCS:
      case TYVS:
        readVbn();
        break;

      case CALLG: {
        readVbn();   // arg count is unused
        auto functionId = static_cast<word_t>(readVbn());
        if (isBuiltinId(functionId)) {
          handleBuiltin(static_cast<BuiltinId>(functionId));
        } else {
          Handle<Function> callee(function_->package()->getFunction(functionId));
          enter(callee);
        }
        break;
      }

      case CALLV: {
        auto argCount = readVbn();
        auto methodIndex = readVbn();
        auto receiver = mem<Object*>(stack_->sp(), 0, argCount - 1);
        CHECK_NON_NULL(receiver);
        Handle<Function> callee(Function::cast(receiver->meta()->getData(methodIndex)));
        if (callee->hasBuiltinId()) {
          handleBuiltin(callee->builtinId());
        } else {
          enter(callee);
        }
        break;
      }

      case EQP: eq<Block*>(); break;
      case NEP: ne<Block*>(); break;

      #define INTERPRET_INT_OP(NAME, name)                            \
      case NAME##I8: name<i8>(); break;                               \
      case NAME##I16: name<i16>(); break;                             \
      case NAME##I32: name<i32>(); break;                             \
      case NAME##I64: name<i64>(); break;                             \

      INTERPRET_INT_OP(ADD, add)
      INTERPRET_INT_OP(SUB, sub)
      INTERPRET_INT_OP(MUL, mul)
      INTERPRET_INT_OP(DIV, div)
      INTERPRET_INT_OP(MOD, mod)
      INTERPRET_INT_OP(LSL, lsl)
      INTERPRET_INT_OP(LSR, lsr)
      INTERPRET_INT_OP(ASR, asr)
      INTERPRET_INT_OP(AND, and_)
      INTERPRET_INT_OP(OR, or_)
      INTERPRET_INT_OP(XOR, xor_)
      INTERPRET_INT_OP(EQ, eq)
      INTERPRET_INT_OP(NE, ne)
      INTERPRET_INT_OP(LT, lt)
      INTERPRET_INT_OP(LE, le)
      INTERPRET_INT_OP(GT, gt)
      INTERPRET_INT_OP(GE, ge)
      INTERPRET_INT_OP(NEG, neg)
      INTERPRET_INT_OP(INV, inv)

      #undef INTERPRET_INT_OP

      #define INTERPRET_FLOAT_OP(NAME, name)                          \
      case NAME##F32: name<f32>(); break;                             \
      case NAME##F64: name<f64>(); break;                             \

      INTERPRET_FLOAT_OP(ADD, add)
      INTERPRET_FLOAT_OP(SUB, sub)
      INTERPRET_FLOAT_OP(MUL, mul)
      INTERPRET_FLOAT_OP(DIV, div)
      INTERPRET_FLOAT_OP(EQ, eq)
      INTERPRET_FLOAT_OP(NE, ne)
      INTERPRET_FLOAT_OP(LT, lt)
      INTERPRET_FLOAT_OP(LE, le)
      INTERPRET_FLOAT_OP(GT, gt)
      INTERPRET_FLOAT_OP(GE, ge)
      INTERPRET_FLOAT_OP(NEG, neg)

      #undef INTERPRET_FLOAT_OP

      case NOTB: {
        auto value = pop<bool>();
        push(!value);
        break;
      }

      case TRUNCI8: trunc<i8>(); break;
      case TRUNCI16: trunc<i16>(); break;
      case TRUNCI32: trunc<i32>(); break;

      case TRUNCF32: convert<f64, f32>(); break;

      case SEXTI16_8: convert<i8, i16>(); break;
      case SEXTI32_8: convert<i8, i32>(); break;
      case SEXTI64_8: convert<i8, i64>(); break;
      case SEXTI32_16: convert<i16, i32>(); break;
      case SEXTI64_16: convert<i16, i64>(); break;
      case SEXTI64_32: convert<i32, i64>(); break;

      case ZEXTI16:
      case ZEXTI32:
      case ZEXTI64:
        break;

      case EXTF64: convert<f32, f64>(); break;
      case FCVTI32: convert<f32, i32>(); break;
      case FCVTI64: convert<f64, i64>(); break;
      case ICVTF32: convert<i32, f32>(); break;
      case ICVTF64: convert<i64, f64>(); break;

      case FTOI32:
      case FTOI64:
      case ITOF32:
      case ITOF64:
        break;

      default:
        UNIMPLEMENTED();
    }
  }

  return result;
}


void Interpreter::ensurePointerMap(Handle<Function> function) {
  // It is not safe to enter a function if we don't have pointer maps for it. These can only
  // be generated if all the other functions called by this function are accessible (so we
  // can't do it before all packages are loaded and validated). Since this requires some
  // analysis, we only do it for functions which are actually called.
  if (function->stackPointerMap() == nullptr) {
    auto stackPointerMap = StackPointerMap::buildFrom(vm_->heap(), function);
    function->setStackPointerMap(*stackPointerMap);
  }
}


void Interpreter::handleBuiltin(BuiltinId id) {
  switch (id) {
    case BUILTIN_ROOT_CLASS_TYPEOF_ID: {
      Type* type = Type::tryAllocate(vm_->heap(), 1);
      if (type == nullptr) {
        collectGarbage();
        type = Type::tryAllocate(vm_->heap(), 1);
        CHECK(type != nullptr);
      }
      auto receiver = Object::cast(pop<Block*>());
      auto clas = receiver->meta()->clas();
      ASSERT(clas != nullptr);
      type->initialize(clas);
      push<Block*>(type);
      break;
    }

    case BUILTIN_ROOT_CLASS_CTOR_ID:
    case BUILTIN_EXCEPTION_CTOR_ID:
    case BUILTIN_NULL_POINTER_EXCEPTION_CTOR_ID:
      mem<word_t>(stack_->sp()) = 0;
      break;

    case BUILTIN_TYPE_CTOR_ID: {
      auto clas = Class::cast(pop<Block*>());
      auto receiver = Type::cast(pop<Block*>());
      receiver->initialize(clas);
      push<i8>(0);
      break;
    }

    case BUILTIN_TYPE_IS_SUBTYPE_OF_ID: {
      auto other = Type::cast(pop<Block*>());
      auto receiver = Type::cast(pop<Block*>());
      auto result = receiver->isSubtypeOf(other);
      push<i8>(result ? 1 : 0);
      break;
    }

    case BUILTIN_STRING_CONCAT_OP_ID: {
      Handle<String> right(String::cast(pop<Block*>()));
      Handle<String> left(String::cast(pop<Block*>()));
      auto cons = left->tryConcat(vm_->heap(), *right);
      if (cons == nullptr) {
        collectGarbage();
        cons = left->tryConcat(vm_->heap(), *right);
        CHECK(cons != nullptr);
      }
      push<Block*>(cons);
      break;
    }

    case BUILTIN_STRING_LT_OP_ID:
      push<i8>(strcmp() < 0 ? 1 : 0);
      break;

    case BUILTIN_STRING_LE_OP_ID:
      push<i8>(strcmp() <= 0 ? 1 : 0);
      break;

    case BUILTIN_STRING_GT_OP_ID:
      push<i8>(strcmp() > 0 ? 1 : 0);
      break;

    case BUILTIN_STRING_GE_OP_ID:
      push<i8>(strcmp() >= 0 ? 1 : 0);
      break;

    case BUILTIN_STRING_EQ_OP_ID:
      push<i8>(strcmp() == 0 ? 1 : 0);
      break;

    case BUILTIN_STRING_NE_OP_ID:
      push<i8>(strcmp() != 0 ? 1 : 0);
      break;

    case BUILTIN_UNIT_TO_STRING_ID: {
      auto string = String::fromUtf8CString(vm_->heap(), "unit");
      mem<Block*>(stack_->sp()) = *string;
      break;
    }

    case BUILTIN_BOOLEAN_TO_STRING_ID: {
      bool value = pop<bool>();
      auto string = String::fromUtf8CString(vm_->heap(), value ? "true" : "false");
      push<Block*>(*string);
      break;
    }

    case BUILTIN_I8_TO_STRING_ID:
      intToString<i8>();
      break;

    case BUILTIN_I16_TO_STRING_ID:
      intToString<i16>();
      break;

    case BUILTIN_I32_TO_STRING_ID:
      intToString<i32>();
      break;

    case BUILTIN_I64_TO_STRING_ID:
      intToString<i64>();
      break;

    case BUILTIN_F32_TO_STRING_ID:
      floatToString<f32>();
      break;

    case BUILTIN_F64_TO_STRING_ID:
      floatToString<f64>();
      break;

    case BUILTIN_PRINT_FUNCTION_ID: {
      auto string = String::cast(pop<Block*>());
      auto stlString = string->toUtf8StlString();
      cout << stlString;
      push<i8>(0);
      break;
    }

    case BUILTIN_READ_FUNCTION_ID: {
      string stlString;
      getline(cin, stlString);
      if (!cin.good()) {
        throwBuiltinException(BUILTIN_EXCEPTION_CLASS_ID);
        break;
      }
      auto string = String::fromUtf8String(vm_->heap(),
                                           reinterpret_cast<const u8*>(stlString.data()),
                                           stlString.length());
      push<Block*>(*string);
      break;
    }

    default:
      UNIMPLEMENTED();
  }
}


Handle<Meta> Interpreter::getMetaForClassId(i64 classId) {
  if (isBuiltinId(classId)) {
    return handle(vm_->roots()->getBuiltinMeta(classId));
  }
  Handle<Class> clas(function_->package()->getClass(classId));
  if (clas->instanceMeta() == nullptr) {
    if (clas->tryBuildInstanceMeta(vm_->heap()) == nullptr) {
      collectGarbage();
      clas->tryBuildInstanceMeta(vm_->heap());
      CHECK(clas->instanceMeta() != nullptr);
    }
  }
  return handle(clas->instanceMeta());
}


void Interpreter::enter(Handle<Function> callee) {
  // Make sure we have pointer maps for the callee before we build a stack frame for it.
  // This may trigger garbage collection (since pointer maps are allocated like everything
  // else), but that's fine since we're at a safepoint, and we haven't built the frame yet.
  ensurePointerMap(callee);

  stack_->align(kWordSize);
  stack_->push(pcOffset_);
  stack_->push(function_ ? *function_ : nullptr);
  stack_->push(stack_->fp());
  stack_->setFp(stack_->sp());
  stack_->setSp(stack_->sp() - callee->localsSize());

  function_ = callee;
  pcOffset_ = 0;
}


void Interpreter::leave() {
  word_t parametersSize = function_->parametersSize();
  Address fp = stack_->fp();
  pcOffset_ = mem<word_t>(fp, kCallerPcOffsetOffset);
  auto caller = mem<Function*>(fp, kFunctionOffset);
  function_ = caller ? Handle<Function>(caller) : Handle<Function>();
  stack_->setSp(fp + kFrameControlSize + parametersSize);
  fp = mem<Address>(fp);
  stack_->setFp(fp);
}


void Interpreter::doThrow(Block* exception) {
  if (handlers_.empty()) {
    // If the exception is unhandled, we need to completely unwind the stack and reset the state
    // of the interpreter in case it is used again.
    stack_->resetPointers();
    function_ = Handle<Function>();
    pcOffset_ = kNotSet;
    throw Error("unhandled exception");
  } else {
    // If there is a handler, we pop the exception, unwind the stack, push the exception, and
    // resume execution there.
    Handler handler = handlers_.back();
    handlers_.pop_back();
    Address fp = handler.fpOffset + stack_->base();
    stack_->push(pcOffset_);
    for (auto frame : **stack_) {
      if (frame.fp() == fp)
        break;
      function_ = handle(frame.function());
    }
    stack_->setFramePointerOffset(handler.fpOffset);
    stack_->setStackPointerOffset(handler.spOffset);
    pcOffset_ = handler.pcOffset;
    push(exception);
  }
}


ptrdiff_t Interpreter::localOffsetFromIndex(i64 index) {
  if (index >= 0) {
    // parameter. 0 is the first parameter (highest address on stack).
    return kFrameControlSize + function_->parameterOffset(static_cast<word_t>(index));
  } else {
    // local. -1 is the first local, and they grow down.
    // TODO: this assumes all locals are word-sized.
    return static_cast<ptrdiff_t>(index) * static_cast<ptrdiff_t>(kWordSize);
  }
}


Address Interpreter::localAddressFromOffset(ptrdiff_t offset) {
  Address fp = stack_->fp();
  return fp + offset;
}


Address Interpreter::localAddressFromIndex(i64 index) {
  return localAddressFromOffset(localOffsetFromIndex(index));
}


i64 Interpreter::readVbn() {
  return codeswitch::internal::readVbn(function_->instructionsStart(), &pcOffset_);
}


void Interpreter::throwBuiltinException(BuiltinId id) {
  // TODO: ensure this allocation succeeds because we don't have a pointer map here.
  auto exnMeta = vm_->roots()->getBuiltinMeta(id);
  auto exn = Object::tryAllocate(vm_->heap(), exnMeta);
  doThrow(exn);
}


void Interpreter::collectGarbage() {
  stack_->push(pcOffset_);
  GC gc(vm_->heap());
  gc.collectGarbage();
  stack_->pop<word_t>();
}


template <typename T>
void Interpreter::loadObject() {
  auto index = readVbn();
  auto block = pop<Block*>();
  if (block == nullptr) {
    throwBuiltinException(BUILTIN_NULL_POINTER_EXCEPTION_CLASS_ID);
    return;
  }
  auto offset = block->meta()->clas()->findFieldOffset(index);
  auto value = mem<T>(block, offset);
  push(value);
}


template <typename T>
void Interpreter::storeObject() {
  auto index = readVbn();
  auto block = pop<Block*>();
  auto value = pop<T>();
  if (block == nullptr) {
    throwBuiltinException(BUILTIN_NULL_POINTER_EXCEPTION_CLASS_ID);
    return;
  }
  auto offset = block->meta()->clas()->findFieldOffset(index);
  mem<T>(block, offset) = value;
}


#define DEFINE_BINOP(name, op)                                    \
template <typename T>                                                 \
void Interpreter::name() {                                            \
  auto right = pop<T>();                                              \
  auto left = pop<T>();                                               \
  push(left op right);                                                \
}                                                                     \

DEFINE_BINOP(add, +)
DEFINE_BINOP(sub, -)
DEFINE_BINOP(mul, *)
DEFINE_BINOP(div, /)
DEFINE_BINOP(mod, %)
DEFINE_BINOP(lsl, <<)
DEFINE_BINOP(asr, >>)
DEFINE_BINOP(and_, &)
DEFINE_BINOP(or_, |)
DEFINE_BINOP(xor_, ^)
DEFINE_BINOP(eq, ==)
DEFINE_BINOP(ne, !=)
DEFINE_BINOP(lt, <)
DEFINE_BINOP(le, <=)
DEFINE_BINOP(gt, >)
DEFINE_BINOP(ge, >=)

#undef DEFINE_BINOP

// We don't define lsr with the macro because we need to do an unsigned shift. C++ doesn't have
// separate operators for logical and arithmetic shifts.
template <typename T>
void Interpreter::lsr() {
  auto right = static_cast<u64>(pop<T>());
  auto left = static_cast<u64>(pop<T>());
  push(left >> right);
}


template <typename T>
void Interpreter::inv() {
  auto value = pop<T>();
  push(~value);
}


template <typename T>
void Interpreter::neg() {
  auto value = pop<T>();
  push(-value);
}


template <typename T>
void Interpreter::trunc() {
  auto value = static_cast<i64>(pop<T>());
  push(value);
}


template <typename From, typename To>
void Interpreter::convert() {
  auto from = pop<From>();
  auto to = static_cast<To>(from);
  push<To>(to);
}


int Interpreter::strcmp() {
  auto right = String::cast(pop<Block*>());
  auto left = String::cast(pop<Block*>());
  auto cmp = left->compare(right);
  return cmp;
}


template <typename T>
void Interpreter::intToString() {
  auto value = static_cast<i64>(pop<T>());
  stringstream stream;
  stream << value;
  auto stlString = stream.str();
  auto size = stlString.length();   // same as length since these should be ascii chars
  auto csString = String::fromUtf8String(vm_->heap(),
                                         reinterpret_cast<const u8*>(stlString.data()),
                                         size, size);
  push<Block*>(*csString);
}


template <typename T>
void Interpreter::floatToString() {
  auto value = pop<T>();
  stringstream stream;
  stream << value;
  auto stlString = stream.str();
  auto size = stlString.length();
  auto csString = String::fromUtf8String(vm_->heap(),
                                         reinterpret_cast<const u8*>(stlString.data()),
                                         size, size);
  push<Block*>(*csString);
}

}
}