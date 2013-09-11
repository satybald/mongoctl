__author__ = 'abdul'

import os
import re

from mongoctl.mongoctl_logging import *
from mongoctl import config
from mongoctl.errors import MongoctlException
from mongoctl.utils import is_exe, which, resolve_path, execute_command
from mongoctl.mongo_version import version_obj

###############################################################################
# CONSTS
###############################################################################

MONGO_HOME_ENV_VAR = "MONGO_HOME"

MONGO_VERSIONS_ENV_VAR = "MONGO_VERSIONS"

# VERSION CHECK PREFERENCE CONSTS
VERSION_PREF_EXACT = 0
VERSION_PREF_GREATER = 1
VERSION_PREF_MAJOR_GE = 2
VERSION_PREF_LATEST_STABLE = 3
VERSION_PREF_EXACT_OR_MINOR = 4

def extract_mongo_exe_options(parsed_args, supported_options):
    options_extract = {}

    # Iterating over parsed options dict
    # Yeah in a hacky way since there is no clean documented way of doing that
    # See http://bugs.python.org/issue11076 for more details
    # this should be changed when argparse provides a cleaner way

    for (option_name,option_val) in parsed_args.__dict__.items():
        if option_name in supported_options and option_val is not None:
            options_extract[option_name] = option_val

    return options_extract



###############################################################################
def get_mongo_executable(server_version,
                         executable_name,
                         version_check_pref=VERSION_PREF_EXACT,
                         install_compatible=False):

    mongo_home = os.getenv(MONGO_HOME_ENV_VAR)
    mongo_installs_dir = config.get_mongodb_installs_dir()


    ver_disp = "[Unspecified]" if server_version is None else server_version
    log_verbose("Looking for a compatible %s for mongoVersion=%s." %
                (executable_name, ver_disp))
    exe_version_tuples = find_all_executables(executable_name)

    if len(exe_version_tuples) > 0:
        selected_exe = best_executable_match(executable_name,
                                             exe_version_tuples,
                                             server_version,
                                             version_check_pref=
                                             version_check_pref)
        if selected_exe is not None:
            log_info("Using %s at '%s' version '%s'..." %
                     (executable_name,
                      selected_exe.path,
                      selected_exe.version))
            return selected_exe

    ## ok nothing found at all. wtf case
    msg = ("Unable to find a compatible '%s' executable "
           "for version %s. You may need to run 'mongoctl install-mongodb %s' to install it.\n\n"
           "Here is your enviroment:\n\n"
           "$PATH=%s\n\n"
           "$MONGO_HOME=%s\n\n"
           "mongoDBInstallationsDirectory=%s (in mongoctl.config)" %
           (executable_name, ver_disp, ver_disp,
            os.getenv("PATH"),
            mongo_home,
            mongo_installs_dir))



    if install_compatible:
        log_warning(msg)
        log_info("Installing a compatible MongoDB...")
        new_mongo_home = install_mongodb(server_version)
        new_exe =  get_mongo_home_exe(new_mongo_home, executable_name)
        return mongo_exe_object(new_exe, version_obj(server_version))



    raise MongoctlException(msg)

###############################################################################
def find_all_executables(executable_name):
    # create a list of all available executables found and then return the best
    # match if applicable
    executables_found = []

    ####### Look in $PATH
    path_executable = which(executable_name)
    if path_executable is not None:
        add_to_executables_found(executables_found, path_executable)

    #### Look in $MONGO_HOME if set
    mongo_home = os.getenv(MONGO_HOME_ENV_VAR)

    if mongo_home is not None:
        mongo_home = resolve_path(mongo_home)
        mongo_home_exe = get_mongo_home_exe(mongo_home, executable_name)
        add_to_executables_found(executables_found, mongo_home_exe)
        # Look in mongod_installs_dir if set
    mongo_installs_dir = config.get_mongodb_installs_dir()

    if mongo_installs_dir is not None:
        if os.path.exists(mongo_installs_dir):
            for mongo_installation in os.listdir(mongo_installs_dir):
                child_mongo_home = os.path.join(mongo_installs_dir,
                                                mongo_installation)

                child_mongo_exe = get_mongo_home_exe(child_mongo_home,
                                                     executable_name)

                add_to_executables_found(executables_found, child_mongo_exe)

    return get_exe_version_tuples(executables_found)

###############################################################################
def add_to_executables_found(executables_found, executable):
    if is_valid_mongo_exe(executable):
        if executable not in executables_found:
            executables_found.append(executable)
    else:
        log_verbose("Not a valid executable '%s'. Skipping..." % executable)

###############################################################################
def best_executable_match(executable_name,
                          exe_version_tuples,
                          version_str,
                          version_check_pref=VERSION_PREF_EXACT):
    version = version_obj(version_str)
    match_func = exact_exe_version_match

    exe_versions_str = exe_version_tuples_to_strs(exe_version_tuples)

    log_verbose("Found the following %s's. Selecting best match "
                "for version %s\n%s" %(executable_name, version_str,
                                       exe_versions_str))

    if version is None:
        log_verbose("mongoVersion is null. "
                    "Selecting default %s" % executable_name)
        match_func = default_match
    elif version_check_pref == VERSION_PREF_LATEST_STABLE:
        match_func = latest_stable_exe
    elif version_check_pref == VERSION_PREF_MAJOR_GE:
        match_func = major_ge_exe_version_match
    elif version_check_pref == VERSION_PREF_EXACT_OR_MINOR:
        match_func = exact_or_minor_exe_version_match

    return match_func(executable_name, exe_version_tuples, version)

###############################################################################
def default_match(executable_name, exe_version_tuples, version):
    default_exe = latest_stable_exe(executable_name, exe_version_tuples)
    if default_exe is None:
        log_verbose("No stable %s found. Looking for any latest available %s "
                    "..." % (executable_name, executable_name))
        default_exe = latest_exe(executable_name, exe_version_tuples)
    return default_exe

###############################################################################
def exact_exe_version_match(executable_name, exe_version_tuples, version):

    for mongo_exe,exe_version in exe_version_tuples:
        if exe_version == version:
            return mongo_exe_object(mongo_exe, exe_version)

    return None

###############################################################################
def latest_stable_exe(executable_name, exe_version_tuples, version=None):
    log_verbose("Find the latest stable %s" % executable_name)
    # find greatest stable exe
    # hold values in a list of (exe,version) tuples
    stable_exes = []
    for mongo_exe,exe_version in exe_version_tuples:
        # get the release number (e.g. A.B.C, release number is B here)
        release_num = exe_version.parts[0][1]
        # stable releases are the even ones
        if (release_num % 2) == 0:
            stable_exes.append((mongo_exe, exe_version))

    return latest_exe(executable_name, stable_exes)

###############################################################################
def latest_exe(executable_name, exe_version_tuples, version=None):

    # Return nothing if nothing compatible
    if len(exe_version_tuples) == 0:
        return None
        # sort desc by version
    exe_version_tuples.sort(key=lambda t: t[1], reverse=True)

    exe = exe_version_tuples[0]
    return mongo_exe_object(exe[0], exe[1])

###############################################################################
def major_ge_exe_version_match(executable_name, exe_version_tuples, version):
    # find all compatible exes then return closet match (min version)
    # hold values in a list of (exe,version) tuples
    compatible_exes = []
    for mongo_exe,exe_version in exe_version_tuples:
        if exe_version.parts[0][0] >= version.parts[0][0]:
            compatible_exes.append((mongo_exe, exe_version))

    # Return nothing if nothing compatible
    if len(compatible_exes) == 0:
        return None
        # find the best fit
    compatible_exes.sort(key=lambda t: t[1])
    exe = compatible_exes[-1]
    return mongo_exe_object(exe[0], exe[1])

###############################################################################
def exact_or_minor_exe_version_match(executable_name,
                                     exe_version_tuples,
                                     version):
    """
    IF there is an exact match then use it
     OTHERWISE try to find a minor version match
    """
    exe = exact_exe_version_match(executable_name,
                                  exe_version_tuples,
                                  version)

    if not exe:
        exe = minor_exe_version_match(executable_name,
                                      exe_version_tuples,
                                      version)
    return exe

###############################################################################
def minor_exe_version_match(executable_name,
                            exe_version_tuples,
                            version):

    # hold values in a list of (exe,version) tuples
    compatible_exes = []
    for mongo_exe,exe_version in exe_version_tuples:
        # compatible ==> major + minor equality
        if (exe_version.parts[0][0] == version.parts[0][0] and
                    exe_version.parts[0][1] == version.parts[0][1]):
            compatible_exes.append((mongo_exe, exe_version))

    # Return nothing if nothing compatible
    if len(compatible_exes) == 0:
        return None
        # find the best fit
    compatible_exes.sort(key=lambda t: t[1])
    exe = compatible_exes[-1]
    return mongo_exe_object(exe[0], exe[1])

###############################################################################
def get_exe_version_tuples(executables):
    exe_ver_tuples = []
    for mongo_exe in executables:
        try:
            exe_version = mongo_exe_version(mongo_exe)
            exe_ver_tuples.append((mongo_exe, exe_version))
        except Exception, e:
            log_verbose("Skipping executable '%s': %s" % (mongo_exe, e))

    return exe_ver_tuples

###############################################################################
def exe_version_tuples_to_strs(exe_ver_tuples):
    strs = []
    for mongo_exe,exe_version in exe_ver_tuples:
        strs.append("%s = %s" % (mongo_exe, exe_version))
    return "\n".join(strs)

###############################################################################
def is_valid_mongo_exe(path):
    return path is not None and is_exe(path)


###############################################################################
def get_mongo_home_exe(mongo_home, executable_name):
    return os.path.join(mongo_home, 'bin', executable_name)

###############################################################################
def mongo_exe_version(mongo_exe):
    try:
        re_expr = "v?((([0-9]+)\.([0-9]+)\.([0-9]+))([^, ]*))"
        vers_spew = execute_command([mongo_exe, "--version"])
        # only take first line of spew
        vers_spew = vers_spew.split('\n')[0]
        vers_grep = re.findall(re_expr, vers_spew)
        full_version = vers_grep[-1][0]
        result = version_obj(full_version)
        if result is not None:
            return result
        else:
            raise MongoctlException("Cannot parse mongo version from the"
                                    " output of '%s --version'" % mongo_exe)
    except Exception, e:
        raise MongoctlException("Unable to get mongo version of '%s'."
                                " Cause: %s" % (mongo_exe, e))

###############################################################################
class MongoExeObject():
    pass

###############################################################################
def mongo_exe_object(exe_path, exe_version):
    exe_obj = MongoExeObject()
    exe_obj.path =  exe_path
    exe_obj.version =  exe_version

    return exe_obj