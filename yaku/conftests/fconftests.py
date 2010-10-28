"""
Fortran-specific configuration tests
"""
import sys
import copy
import os
import types
import re
import shlex

from yaku.errors \
    import \
        TaskRunFailure, WindowsError
from yaku.task_manager \
    import \
        CompiledTaskGen
from yaku.scheduler \
    import \
        run_tasks
from yaku.utils \
    import \
        ensure_dir
from yaku.tools.ctasks \
    import \
        apply_libdir
from yaku.conf \
    import \
       generate_config_h, ConfigureContext, \
       write_log, create_file, create_conf_blddir
from yaku.conftests.fconftests_imp \
    import \
        is_output_verbose, parse_flink

import subprocess

FC_VERBOSE_FLAG = "FC_VERBOSE_FLAG"
FC_RUNTIME_LDFLAGS = "FC_RUNTIME_LDFLAGS"
FC_DUMMY_MAIN = "FC_DUMMY_MAIN"

def create_fprogram_conf_taskgen(conf, name, body):
    # FIXME: make tools modules available through config context
    ftool = __import__("fortran")
    builder = ftool.fprogram_task

    old_root, new_root = create_conf_blddir(conf, name, body)
    try:
        conf.bld_root = new_root
        return _create_fbinary_conf_taskgen(conf, name, body, builder)
    finally:
        conf.bld_root = old_root

def create_fstatic_conf_taskgen(conf, name, body):
    # FIXME: make tools modules available through config context
    ctool = __import__("ctasks")
    builder = ctool.static_link_task

    old_root, new_root = create_conf_blddir(conf, name, body)
    try:
        conf.bld_root = new_root
        return _create_fbinary_conf_taskgen(conf, name, body, builder)
    finally:
        conf.bld_root = old_root

def _create_fbinary_conf_taskgen(conf, name, body, builder):
    # FIXME: refactor commonalities between configuration taskgens
    code = body
    sources = [create_file(conf, code, name, ".f")]

    task_gen = CompiledTaskGen("conf", conf,
                               sources, name)
    task_gen.env.update(copy.deepcopy(conf.env))
    apply_libdir(task_gen)

    tasks = task_gen.process()
    link_task = builder(task_gen, name)

    tasks.extend(link_task)
    conf.last_task = tasks[-1]

    for t in tasks:
        t.disable_output = True
        t.log = conf.log

    succeed = False
    explanation = None
    try:
        run_tasks(conf, tasks)
        succeed = True
    except TaskRunFailure, e:
        explanation = str(e)

    write_log(conf, conf.log, tasks, code, succeed, explanation)
    return succeed

def check_fcompiler(conf, msg=None):
    code = """\
       program main
       end
"""
    if msg is None:
        conf.start_message("Checking whether Fortran compiler works")
    else:
        conf.start_message(msg)
    ret = create_fprogram_conf_taskgen(conf, "check_fcompiler", code)
    if ret:
        conf.end_message("yes")
    else:
        conf.end_message("no !")
    return ret

def check_fortran_verbose_flag(conf):
    code = """\
       program main
       end
"""
    conf.start_message("Checking for verbose flag")
    if not conf.builders["ctasks"].configured:
        raise ValueError("'ctasks'r needs to be configured first!")
    if sys.platform == "win32":
        conf.end_message("none needed")
        conf.env[FC_VERBOSE_FLAG] = []
        return True
    for flag in ["-v", "--verbose", "-V", "-verbose"]:
        old = copy.deepcopy(conf.env["F77_LINKFLAGS"])
        try:
            conf.env["F77_LINKFLAGS"].append(flag)
            ret = create_fprogram_conf_taskgen(conf,
                    "check_fc_verbose", code)
            if not ret:
                continue
            stdout = conf.get_stdout(conf.last_task)
            if ret and is_output_verbose(stdout):
                conf.end_message(flag)
                conf.env[FC_VERBOSE_FLAG] = flag
                return True
        finally:
            conf.env["F77_LINKFLAGS"] = old
    conf.end_message("failed !")
    return False

def check_fortran_runtime_flags(conf):
    if not conf.builders["ctasks"].configured:
        raise ValueError("'ctasks'r needs to be configured first!")
    if sys.platform == "win32":
        return _check_fortran_runtime_flags_win32(conf)
    else:
        return _check_fortran_runtime_flags(conf)

def _check_fortran_runtime_flags_win32(conf):
    if conf.env["cc_type"] == "msvc":
        conf.start_message("Checking for fortran runtime flags")
        conf.end_message("none needed")
        conf.env[FC_RUNTIME_LDFLAGS] = []
    else:
        raise NotImplementedError("GNU support on win32 not ready")

def _check_fortran_runtime_flags(conf):
    if not conf.env.has_key(FC_VERBOSE_FLAG):
        raise ValueError("""\
You need to call check_fortran_verbose_flag before getting runtime
flags (or to define the %s variable)""" % FC_VERBOSE_FLAG)
    code = """\
       program main
       end
"""

    conf.start_message("Checking for fortran runtime flags")

    old = copy.deepcopy(conf.env["F77_LINKFLAGS"])
    try:
        conf.env["F77_LINKFLAGS"].append(conf.env["FC_VERBOSE_FLAG"])
        ret = create_fprogram_conf_taskgen(conf, "check_fc", code)
        if ret:
            stdout = conf.get_stdout(conf.last_task)
            flags = parse_flink(stdout)
            conf.end_message("%r" % " ".join(flags))
            conf.env[FC_RUNTIME_LDFLAGS] = flags
            return True
        else:
            conf.end_message("failed !")
            return False
    finally:
        conf.env["F77_LINKFLAGS"] = old
    return False

def check_fortran_dummy_main(conf):
    code_tpl = """\
#ifdef __cplusplus
        extern "C"
#endif
int %(main)s()
{
    return 1;
}

int main()
{
    return 0;
}
"""

    conf.start_message("Checking whether fortran needs dummy main")

    old = copy.deepcopy(conf.env["F77_LINKFLAGS"])
    try:
        conf.env["F77_LINKFLAGS"].extend(conf.env[FC_RUNTIME_LDFLAGS])
        ret = conf.builders["ctasks"].try_program("check_fc_dummy_main",
                code_tpl % {"main": "FC_DUMMY_MAIN"})
        if ret:
            conf.end_message("none")
            conf.env[FC_DUMMY_MAIN] = None
            return True
        else:
            conf.end_message("failed !")
            return False
    finally:
        conf.env["F77_LINKFLAGS"] = old

def check_fortran_mangling(conf):
    subr = """
      subroutine foobar()
      return
      end
      subroutine foo_bar()
      return
      end
"""
    main_tmpl = """
      int %s() { return 1; }
"""
    prog_tmpl = """
      void %(foobar)s(void);
      void %(foo_bar)s(void);
      int main() {
      %(foobar)s();
      %(foo_bar)s();
      return 0;
      }
"""

    conf.start_message("Checking fortran mangling scheme")
    old = {}
    for k in ["F77_LINKFLAGS", "LIBS", "LIBDIR"]:
        old[k] = copy.deepcopy(conf.env[k])
    try:
        mangling_lib = "check_fc_mangling_lib"
        ret = create_fstatic_conf_taskgen(conf, mangling_lib, subr)
        if ret:
            if conf.env[FC_DUMMY_MAIN] is not None:
                main = main_tmpl % conf.env["FC_DUMMY_MAIN"]
            else:
                main = ""
            conf.env["LIBS"].insert(0, mangling_lib)
            libdir = conf.last_task.outputs[-1].parent.abspath()
            conf.env["LIBDIR"].insert(0, libdir)

            for u, du, case in mangling_generator():
                names = {"foobar": mangle_func("foobar", u, du, case),
                         "foo_bar": mangle_func("foo_bar", u, du, case)}
                prog = prog_tmpl % names
                name = "check_fc_mangling_main"
                def _name(u):
                    if u == "_":
                        return "u"
                    else:
                        return "nu"
                name += "_%s_%s_%s" % (_name(u), _name(du), case)
                ret = conf.builders["ctasks"].try_program(name, main + prog)
                if ret:
                    conf.env["FC_MANGLING"] = (u, du, case)
                    conf.end_message("%r %r %r" % (u, du, case))
                    return
            conf.end_message("failed !")
        else:
            conf.end_message("failed !")

    finally:
        for k in old:
            conf.env[k] = old[k]

def mangling_generator():
    for under in ['_', '']:
        for double_under in ['', '_']:
            for case in ["lower", "upper"]:
                yield under, double_under, case

def mangle_func(name, under, double_under, case):
    return getattr(name, case)() + under + (name.find("_") != -1 and double_under or '')
