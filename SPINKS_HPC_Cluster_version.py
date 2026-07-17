#%% STEP 1. CLUSTER AUTOMATED MINIMA + INTERPOLATION + MECP OPTIMIZATION

import os
import re
import time
import socket
import sys
import getpass
import posixpath
import paramiko
import numpy as np
import matplotlib.pyplot as plt

print(r'''
      
This is the cluster version of the workflow. It is designed to be run
within the Spyder Python environment and interfaces with ORCA 5.0.4
through SSH/SFTP. Calculations are submitted to a remote OpenPBS-style
queue.

Before continuing, make sure that you have access to a remote
high-performance computing (HPC) environment and that ORCA can be
executed successfully on that system. The following cluster settings are
used as defaults in the generated bash submission files:

ORCA executable directory:
    /usr/local/apps/orca/5.0.4

PATH update:
    export PATH=/usr/local/apps/orca/5.0.4:$PATH

MPI module:
    module load openmpi/4.1.5

ORCA execution command:
    /usr/local/apps/orca/5.0.4/orca your_file_name.inp > your_file_name.out

If your cluster uses a different ORCA installation directory, MPI module,
      queueing system, or job-submission syntax, you may modify the generated
      bash script template before any calculations are submitted.
      
      
====================================================================
 STEP 1 | CLUSTER VERSION
 Automated spin-state minima, interpolation scan, and MECP search
====================================================================

''')

IS_SPYDER = ("spyder_kernels" in sys.modules) or ("spyder" in sys.modules)
RUN_MODE = "CLUSTER"
workflow_mode = "MECP_ONLY"
run_mecp_optimization = True

# ============================================================
# General utilities
# ============================================================

def safe_input(prompt=""):
    return input(prompt)


def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def subsection(title):
    print("\n" + "-" * 72)
    print(title)
    print("-" * 72 + "\n")


def ask_int(prompt, minimum=None):
    while True:
        value = safe_input(prompt).strip()
        try:
            value = int(value)
            if minimum is not None and value < minimum:
                print(f"  Input must be at least {minimum}.")
                continue
            return value
        except ValueError:
            print("  Please enter an integer.")


def ask_yes_no(prompt, default=None):
    if default is True:
        suffix = " (Y/N, default Y): "
    elif default is False:
        suffix = " (Y/N, default N): "
    else:
        suffix = " (Y/N): "
    while True:
        ans = safe_input(prompt + suffix).strip().lower()
        if ans == "" and default is not None:
            return bool(default)
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  Please answer Y or N.")


def parse_int_list(text):
    return [int(x) for x in text.replace(" ", "").split(",") if x]


def read_geometry_block(title):
    print(f"\n{title}")
    print("""
Please provide Cartesian coordinates using the format:

atom_label   x_coordinate   y_coordinate   z_coordinate

An example geometry block is shown below:

O    0.00000000000000    0.00000000818129     0.11690363084563
H    0.00000000000000    0.76528672468584    -0.46747581229803
H    0.00000000000000   -0.76528673286712    -0.46747581854760
""")
    print("Press ENTER on an empty line when the geometry is complete.\n")
    lines = []
    while True:
        line = safe_input()
        if line.strip() == "":
            break
        lines.append(line.rstrip())
    if not lines:
        raise ValueError("No geometry was provided.")
    return "\n".join(lines)


def multiplicity_name(mult):
    return {
        1: "singlet", 2: "doublet", 3: "triplet", 4: "quartet", 5: "quintet",
        6: "sextet", 7: "septet", 8: "octet", 9: "nonet", 10: "decet"
    }.get(int(mult), f"mult{mult}")


def collect_multiline_until_eof():
    lines = []
    while True:
        line = safe_input()
        if line.strip() == "EOF":
            break
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def validate_placeholders(text, placeholders, label):
    missing = [p for p in placeholders if p not in text]
    if missing:
        raise RuntimeError(f"The edited {label} template is missing: " + ", ".join(missing))


def minimum_input_contains_required_keywords(inp_text):
    for line in inp_text.splitlines():
        stripped = line.strip()

        if stripped.startswith("!"):
            tokens = stripped.lower().split()
            has_opt = "opt" in tokens
            has_freq = "freq" in tokens
            return has_opt, has_freq

    return False, False


def enforce_minimum_input_requirements(inp_text, job_label):
    while True:
        has_opt, has_freq = minimum_input_contains_required_keywords(inp_text)

        if has_opt and has_freq:
            return inp_text

        section("STEP 1 INPUT VALIDATION")

        print(f"The ORCA input for {job_label} is missing one or more required keywords.\n")

        if not has_opt:
            print("Missing keyword: Opt")

        if not has_freq:
            print("Missing keyword: Freq")

        print(
            "\nFor Step 1, both geometry optimization (Opt) and frequency "
            "analysis (Freq) are required."
        )
        print(
            "The optimized geometry is used for interpolation and MECP "
            "generation, while vibrational frequencies are required for "
            "zero-point energy correction of the barrier."
        )

        print("\nPlease re-edit the input file and submit a corrected version.\n")
        print(inp_text)
        print("\nPaste the corrected input and finish with EOF.\n")

        inp_text = collect_multiline_until_eof()

# ============================================================
# Remote utilities
# ============================================================

def run_ssh(ssh_obj, cmd):
    stdin, stdout, stderr = ssh_obj.exec_command(cmd)
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    return out.strip(), err.strip()


# ============================================================
# Remote utilities with automatic reconnect
# ============================================================

def _close_quiet(obj):
    try:
        obj.close()
    except Exception:
        pass


def _is_ssh_alive(ssh_obj):
    try:
        t = ssh_obj.get_transport()
        return t is not None and t.is_active()
    except Exception:
        return False


def _is_sftp_alive(sftp_obj):
    try:
        sftp_obj.listdir(".")
        return True
    except Exception:
        return False


def reconnect_ssh_sftp(max_tries=5, wait_seconds=5):
    global ssh, sftp

    if _is_ssh_alive(globals().get("ssh", None)) and _is_sftp_alive(globals().get("sftp", None)):
        return

    for attempt in range(1, max_tries + 1):
        print(f"\nSSH/SFTP connection lost. Reconnecting attempt {attempt}/{max_tries}...")

        _close_quiet(globals().get("sftp", None))
        _close_quiet(globals().get("ssh", None))

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(
                cluster_host,
                username=cluster_account,
                password=cluster_password,
                timeout=30,
                banner_timeout=30,
                auth_timeout=30,
                look_for_keys=False,
                allow_agent=False
            )

            sftp = ssh.open_sftp()
            sftp.listdir(".")
            print("SSH/SFTP connection re-established.\n")
            return

        except (paramiko.SSHException, socket.error, EOFError, ConnectionResetError, OSError) as err:
            print(f"Reconnect failed: {type(err).__name__}: {err}")
            time.sleep(wait_seconds)

    raise RuntimeError("Could not re-establish SSH/SFTP connection.")


def run_ssh(ssh_obj, cmd):
    global ssh, sftp

    last_error = None

    for attempt in range(1, 4):
        try:
            reconnect_ssh_sftp()
            stdin, stdout, stderr = ssh.exec_command(cmd)
            out = stdout.read().decode(errors="ignore")
            err = stderr.read().decode(errors="ignore")
            return out.strip(), err.strip()

        except (paramiko.SSHException, socket.error, EOFError, ConnectionResetError, OSError) as err:
            last_error = err
            print(f"\nSSH command interrupted. Reconnecting and retrying {attempt}/3...")
            _close_quiet(globals().get("sftp", None))
            _close_quiet(globals().get("ssh", None))
            time.sleep(3)

    raise RuntimeError(f"SSH command failed after reconnect attempts: {last_error}")


def remote_mkdir_p(sftp_obj, path):
    global sftp
    reconnect_ssh_sftp()

    current = ""
    for part in path.split("/"):
        if not part:
            continue
        current += "/" + part
        try:
            sftp.listdir(current)
        except IOError:
            try:
                sftp.mkdir(current)
            except IOError:
                pass


def remote_file_exists(sftp_obj, path):
    global sftp

    for attempt in range(1, 4):
        try:
            reconnect_ssh_sftp()
            sftp.stat(path)
            return True
        except IOError:
            return False
        except (paramiko.SSHException, socket.error, EOFError, ConnectionResetError, OSError):
            _close_quiet(globals().get("sftp", None))
            _close_quiet(globals().get("ssh", None))
            time.sleep(3)

    return False


def remote_read_text(sftp_obj, path):
    global sftp

    last_error = None

    for attempt in range(1, 4):
        try:
            reconnect_ssh_sftp()
            with sftp.open(path, "r") as f:
                data = f.read()
            return data.decode(errors="ignore") if isinstance(data, bytes) else str(data)

        except (paramiko.SSHException, socket.error, EOFError, ConnectionResetError, OSError) as err:
            last_error = err
            print(f"\nSFTP read interrupted. Reconnecting and retrying {attempt}/3...")
            _close_quiet(globals().get("sftp", None))
            _close_quiet(globals().get("ssh", None))
            time.sleep(3)

    raise RuntimeError(f"Remote file read failed after reconnect attempts: {path}\n{last_error}")


def remote_out_status(ssh_obj, remote_dir, basename):
    cmd = f'''
cd "{remote_dir}"
if [ ! -f "{basename}.out" ]; then
    echo MISSING
elif grep -qi "ORCA TERMINATED NORMALLY" "{basename}.out"; then
    echo OK
elif grep -qi "error termination" "{basename}.out"; then
    echo ERROR
elif grep -qi "ORCA finished by error termination" "{basename}.out"; then
    echo ERROR
else
    echo FAILED
fi
'''
    out, _ = run_ssh(ssh_obj, cmd)
    return out.strip() if out.strip() else "MISSING"


def remote_tail(ssh_obj, remote_dir, filename, n_lines=100):
    out, _ = run_ssh(ssh_obj, f'cd "{remote_dir}" && tail -n {n_lines} "{filename}" 2>/dev/null')
    return out


def archive_remote_job_files(ssh_obj, remote_dir, basename, label):
    cmd = f'''
cd "{remote_dir}"
ARCHIVE_DIR="{label}_{basename}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$ARCHIVE_DIR"
for f in "{basename}".*; do
    if [ -e "$f" ]; then mv "$f" "$ARCHIVE_DIR"/; fi
done
'''
    run_ssh(ssh_obj, cmd)


def get_job_state(qstat_text, short_id):
    if not short_id:
        return None
    for line in qstat_text.splitlines():
        line = line.strip()
        if not line or line.startswith("Job ID"):
            continue
        if short_id in line:
            parts = line.split()
            if len(parts) >= 2:
                return parts[-2]
    return None

# ============================================================
# Parsing and geometry
# ============================================================

def extract_ele_zpe_from_text(out_text, source_label="output"):
    ele, zpe = None, None
    for raw in out_text.splitlines():
        line = raw.strip()
        if line.startswith("Electronic energy"):
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "Eh" and i > 0:
                    ele = float(parts[i - 1])
                    break
        if line.startswith("Zero point energy"):
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "Eh" and i > 0:
                    zpe = float(parts[i - 1])
                    break
    if ele is None:
        raise RuntimeError(f"Could not extract electronic energy from {source_label}")
    if zpe is None:
        raise RuntimeError(f"Could not extract zero-point energy from {source_label}")
    return ele, zpe


def extract_final_sp_energy_from_text(out_text, source_label="output"):
    energies = []
    for line in out_text.splitlines():
        if "FINAL SINGLE POINT ENERGY" in line:
            try:
                energies.append(float(line.split()[-1]))
            except Exception:
                pass
    if not energies:
        raise RuntimeError(f"Could not extract FINAL SINGLE POINT ENERGY from {source_label}")
    return float(energies[-1])


def extract_geometry_from_xyz_text(xyz_text, source_label="xyz"):
    lines = xyz_text.splitlines()
    if len(lines) < 3:
        raise RuntimeError(f"XYZ text is too short: {source_label}")
    natoms = int(lines[0].strip())
    geom = []
    for line in lines[2:2 + natoms]:
        p = line.split()
        if len(p) >= 4:
            geom.append(f"{p[0]:<2} {float(p[1]): 18.12f} {float(p[2]): 18.12f} {float(p[3]): 18.12f}")
    if len(geom) != natoms:
        raise RuntimeError(f"Could not extract all atoms from {source_label}")
    return "\n".join(geom)


def parse_geometry_to_arrays(geom_text):
    symbols, coords = [], []
    for line in geom_text.splitlines():
        p = line.split()
        if len(p) >= 4:
            symbols.append(p[0])
            coords.append([float(p[1]), float(p[2]), float(p[3])])
    if not symbols:
        raise RuntimeError("No valid Cartesian geometry lines were found.")
    return symbols, np.array(coords, dtype=float)


def geometry_from_symbols_coords(symbols, coords):
    return "\n".join(
        f"{s:<2} {xyz[0]: 18.12f} {xyz[1]: 18.12f} {xyz[2]: 18.12f}"
        for s, xyz in zip(symbols, coords)
    )


def interpolate_geometry(geom_a, geom_b, lam):
    sym_a, xyz_a = parse_geometry_to_arrays(geom_a)
    sym_b, xyz_b = parse_geometry_to_arrays(geom_b)
    if sym_a != sym_b:
        raise RuntimeError("Atom order differs between optimized minima. Interpolation is unsafe.")
    if xyz_a.shape != xyz_b.shape:
        raise RuntimeError("Geometry sizes differ between optimized minima.")
    return geometry_from_symbols_coords(sym_a, (1.0 - lam) * xyz_a + lam * xyz_b)

# ============================================================
# ORCA templates
# ============================================================

def default_orca_template(kind):
    if kind == "minimum":
        return '''! {method} {basis} TightSCF SlowConv Opt Freq

%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

%scf
  MaxIter 800
  SOSCFStart 0.01
  LevelShift 0.5
end

%output
  PrintLevel 3
end

* xyz {charge} {mult}
{geom}
*
'''
    if kind == "single_point":
        return '''! {method} {basis} TightSCF SlowConv

%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

%scf
  MaxIter 800
  SOSCFStart 0.01
  LevelShift 0.5
end

%output
  PrintLevel 3
end

* xyz {charge} {mult}
{geom}
*
'''
    if kind == "mecp":
        return '''! {method} {basis} SurfCrossOpt TightSCF SlowConv

%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

%scf
  MaxIter 800
  SOSCFStart 0.01
  LevelShift 0.5
end

%mecp Mult {mult_other}
end

%output
  PrintLevel 3
end

* xyz {charge} {mult}
{geom}
*
'''
    raise ValueError(kind)


def review_orca_templates_once(example_geometry, example_charge, example_mult_a, example_mult_b):
    global ORCA_INPUT_TEMPLATES, ORCA_INPUT_TEMPLATES_WERE_REVIEWED, ORCA_INPUT_TEMPLATE_SOURCE

    if globals().get("ORCA_INPUT_TEMPLATES_WERE_REVIEWED", False):
        return

    section("ORCA INPUT TEMPLATE REVIEW")

    print("Default electronic-structure settings for Step 1 are listed below.")
    print("You will be asked whether you wish to modify these settings before calculations are submitted.")
    print("  Method    : PBE")
    print("  Basis set : def2-TZVP")

    ORCA_INPUT_TEMPLATES = {
        "minimum": default_orca_template("minimum"),
        "single_point": default_orca_template("single_point"),
        "mecp": default_orca_template("mecp"),
    }

    print("\nThe input below is default for low-spin minimum optimization:\n")

    low_spin_example = ORCA_INPUT_TEMPLATES["minimum"].format(
        method=method,
        basis=basis,
        charge=example_charge,
        mult=example_mult_a,
        mult_other=example_mult_b,
        geom=example_geometry,
        nprocs=nprocs,
        maxcore_mb=maxcore_mb
    )

    print(low_spin_example)

    if ask_yes_no("Edit the low-spin ORCA input template before submission?", default=False):
        print("\nPaste the complete edited low-spin minimum template.")
        print("Finish with EOF.\n")
        edited = collect_multiline_until_eof()

        validate_placeholders(
            edited,
            ["{method}", "{basis}", "{charge}", "{mult}", "{geom}", "{nprocs}", "{maxcore_mb}"],
            "low-spin minimum"
        )

        edited = enforce_minimum_input_requirements(edited, "low-spin minimum template")
        ORCA_INPUT_TEMPLATES["minimum"] = edited

    print("\nThe input below is default for high-spin minimum optimization:\n")

    high_spin_example = ORCA_INPUT_TEMPLATES["minimum"].format(
        method=method,
        basis=basis,
        charge=example_charge,
        mult=example_mult_b,
        mult_other=example_mult_a,
        geom=example_geometry,
        nprocs=nprocs,
        maxcore_mb=maxcore_mb
    )

    print(high_spin_example)

    if ask_yes_no("Edit the high-spin ORCA input template before submission?", default=False):
        print("\nPaste the complete edited high-spin minimum template.")
        print("Finish with EOF.\n")
        edited = collect_multiline_until_eof()

        validate_placeholders(
            edited,
            ["{method}", "{basis}", "{charge}", "{mult}", "{geom}", "{nprocs}", "{maxcore_mb}"],
            "high-spin minimum"
        )

        edited = enforce_minimum_input_requirements(edited, "high-spin minimum template")
        ORCA_INPUT_TEMPLATES["minimum"] = edited

    ORCA_INPUT_TEMPLATE_SOURCE = "Default PBE/def2-TZVP Step 1 templates with reviewed minimum inputs"
    ORCA_INPUT_TEMPLATES_WERE_REVIEWED = True

    print("\nORCA input templates finalized.\n")


def edit_failed_input_interactively(inp_text, job_label):
    section("ORCA INPUT REVISION REQUIRED")
    print("WARNING: ORCA did not terminate normally.")
    print(f"The calculation labeled '{job_label}' failed.")
    print("The current input is shown below. Paste a corrected input and finish with EOF.\n")
    print(inp_text)
    return collect_multiline_until_eof()


# ============================================================
# Protected ORCA input repair system
# ============================================================

def normalize_geometry_text(geom_text):
    lines = []
    for line in str(geom_text).splitlines():
        p = line.split()
        if len(p) >= 4:
            lines.append(
                f"{p[0]} {float(p[1]):.10f} {float(p[2]):.10f} {float(p[3]):.10f}"
            )
    return "\n".join(lines)


def split_orca_xyz_block(inp_text):
    lines = inp_text.splitlines()
    xyz_start = None

    for i, line in enumerate(lines):
        if line.strip().lower().startswith("* xyz"):
            xyz_start = i
            break

    if xyz_start is None:
        raise RuntimeError("Could not find '* xyz charge multiplicity' block.")

    xyz_end = None
    for j in range(xyz_start + 1, len(lines)):
        if lines[j].strip() == "*":
            xyz_end = j
            break

    if xyz_end is None:
        raise RuntimeError("Could not find closing '*' for xyz block.")

    pre_xyz = "\n".join(lines[:xyz_start]).rstrip() + "\n"
    xyz_block = "\n".join(lines[xyz_start:xyz_end + 1]).rstrip() + "\n"
    post_xyz = "\n".join(lines[xyz_end + 1:]).rstrip()

    return pre_xyz, xyz_block, post_xyz


# ============================================================
# ORCA reusable DFT settings propagation
# ============================================================

ORCA_JOB_KEYWORDS = {
    "opt", "freq", "numfreq", "engrad", "surfcrossopt",
    "surfcrossnumfreq", "sp"
}

def ask_nonempty_text(prompt, default=None):
    while True:
        ans = safe_input(prompt).strip()
        if ans:
            return ans
        if default is not None:
            return str(default)
        print("  Input cannot be empty.")


def extract_bang_line_from_pre(pre_text):
    for line in str(pre_text).splitlines():
        if line.strip().startswith("!"):
            return line.strip()
    raise RuntimeError("Could not find ORCA ! line in the editable settings block.")


def extract_orca_percent_blocks_from_pre(pre_text, exclude_names=None):
    """Extract every ORCA % block/line from the pre-geometry region."""
    exclude_names = {str(x).lower() for x in (exclude_names or [])}
    lines = str(pre_text).splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped.startswith("%"):
            i += 1
            continue
        block_name = stripped[1:].split()[0].lower()
        block_lines = [line]
        i += 1
        if stripped.lower().endswith(" end") or block_name == "maxcore":
            pass
        else:
            while i < len(lines):
                block_lines.append(lines[i])
                if lines[i].strip().lower() == "end":
                    i += 1
                    break
                i += 1
        if block_name not in exclude_names:
            blocks.append("\n".join(block_lines).rstrip())
    return ("\n\n".join(blocks).strip() + "\n\n") if blocks else ""


def adapt_bang_line_for_job(bang_line, job_type):
    tokens = str(bang_line).strip().split()
    if not tokens or tokens[0] != "!":
        raise RuntimeError("Editable ORCA settings block must start with a ! line.")
    clean = [tok for tok in tokens[1:] if tok.lower() not in ORCA_JOB_KEYWORDS]
    job_type = str(job_type).lower()
    if job_type == "minimum":
        new_body = clean + ["Opt", "Freq"]
    elif job_type == "single_point":
        new_body = clean
    elif job_type == "mecp":
        new_body = clean + ["SurfCrossOpt"]
    elif job_type == "engrad":
        new_body = ["Engrad"] + clean
    elif job_type == "freq":
        new_body = clean + ["SurfCrossNumFreq"]
    else:
        raise RuntimeError(f"Unknown ORCA job_type for header adaptation: {job_type}")
    return "! " + " ".join(new_body).strip()


def build_orca_input_from_pre_xyz(pre_xyz_text, charge_i, mult_i, geom_i):
    pre = str(pre_xyz_text).rstrip()
    return f"{pre}\n\n* xyz {int(charge_i)} {int(mult_i)}\n{geom_i}\n*\n"


def make_dft_pre_xyz(job_type, job_specific_blocks=""):
    base_pre = globals().get("DFT_REVIEWED_PRE_XYZ", "")
    if not base_pre.strip():
        base_pre = f"""! {method} {basis} TightSCF SlowConv Opt Freq

%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

%scf
  MaxIter 800
  SOSCFStart 0.01
  LevelShift 0.5
end

%output
  PrintLevel 3
end
"""
    header = adapt_bang_line_for_job(extract_bang_line_from_pre(base_pre), job_type)
    blocks = globals().get("ORCA_TRANSFER_BLOCKS_ALL", "")
    if not blocks.strip():
        blocks = extract_orca_percent_blocks_from_pre(base_pre)
    text = header.rstrip() + "\n\n"
    if blocks.strip():
        text += blocks.rstrip() + "\n\n"
    if str(job_specific_blocks).strip():
        text += str(job_specific_blocks).strip() + "\n\n"
    return text.rstrip() + "\n"


def apply_dft_settings_to_input(inp_text, job_type, job_specific_blocks=""):
    _, xyz_block, post_xyz = split_orca_xyz_block(inp_text)
    new_pre = make_dft_pre_xyz(job_type, job_specific_blocks=job_specific_blocks)
    out = new_pre.rstrip() + "\n\n" + xyz_block
    if post_xyz.strip():
        out += "\n" + post_xyz.strip() + "\n"
    return out



def parse_xyz_charge_mult_geom(inp_text):
    _, xyz_block, _ = split_orca_xyz_block(inp_text)
    lines = xyz_block.splitlines()

    first = lines[0].split()
    if len(first) < 4:
        raise RuntimeError("Malformed xyz line. Expected: * xyz charge multiplicity")

    charge_i = int(first[2])
    mult_i = int(first[3])
    geom_i = "\n".join(lines[1:-1])

    return charge_i, mult_i, geom_i


def first_bang_line(inp_text):
    for line in inp_text.splitlines():
        if line.strip().startswith("!"):
            return line.strip()
    return ""


def require_keyword_in_bang(inp_text, keyword, label):
    bang = first_bang_line(inp_text).lower()
    if keyword.lower() not in bang.split():
        raise RuntimeError(
            f"{label}: required keyword '{keyword}' is missing from the ORCA ! line."
        )


def require_text_in_bang(inp_text, text, label):
    bang = first_bang_line(inp_text).lower()
    if str(text).lower() not in bang:
        raise RuntimeError(
            f"{label}: required setting '{text}' is missing from the ORCA ! line."
        )


def require_exact_xyz(inp_text, expected_charge, expected_mult, expected_geom, label):
    charge_i, mult_i, geom_i = parse_xyz_charge_mult_geom(inp_text)

    if int(charge_i) != int(expected_charge):
        raise RuntimeError(
            f"{label}: charge was changed. It must remain {expected_charge}."
        )

    if int(mult_i) != int(expected_mult):
        raise RuntimeError(
            f"{label}: multiplicity was changed. It must remain {expected_mult}."
        )

    if normalize_geometry_text(geom_i) != normalize_geometry_text(expected_geom):
        raise RuntimeError(
            f"{label}: geometry was changed. Geometry is protected for this step."
        )


def require_mecp_mult(inp_text, expected_mecp_mult, label):
    m = re.search(r"%mecp\s+Mult\s+([0-9]+)", inp_text, flags=re.IGNORECASE)

    if not m:
        raise RuntimeError(
            f"{label}: required '%mecp Mult {expected_mecp_mult}' block is missing."
        )

    found = int(m.group(1))

    if found != int(expected_mecp_mult):
        raise RuntimeError(
            f"{label}: %mecp Mult was changed to {found}. "
            f"It must remain {expected_mecp_mult}."
        )


def validate_repaired_input(inp_text, policy, locked, label):
    if policy == "free_full":
        return inp_text

    if policy == "free_minimum":
        return enforce_minimum_input_requirements(inp_text, label)

    if policy == "interp_header_only":
        require_text_in_bang(inp_text, locked["method"], label)
        require_text_in_bang(inp_text, locked["basis"], label)
        return inp_text

    if policy == "mecp_restricted":
        require_text_in_bang(inp_text, locked["method"], label)
        require_text_in_bang(inp_text, locked["basis"], label)
        require_keyword_in_bang(inp_text, "SurfCrossOpt", label)
        require_exact_xyz(
            inp_text,
            locked["charge"],
            locked["mult"],
            locked["geom"],
            label
        )
        require_mecp_mult(inp_text, locked["mecp_mult"], label)
        return inp_text

    if policy == "engrad_restricted":
        require_keyword_in_bang(inp_text, "Engrad", label)
        require_text_in_bang(inp_text, locked["method"], label)
        require_text_in_bang(inp_text, locked["basis"], label)
        require_exact_xyz(
            inp_text,
            locked["charge"],
            locked["mult"],
            locked["geom"],
            label
        )
        return inp_text

    if policy == "rohf_restricted":
        require_keyword_in_bang(inp_text, locked["method"], label)
        require_text_in_bang(inp_text, locked["basis"], label)
        require_exact_xyz(
            inp_text,
            locked["charge"],
            locked["mult"],
            locked["geom"],
            label
        )
        return inp_text

    if policy == "soc_restricted":
        require_text_in_bang(inp_text, locked["basis"], label)
        require_exact_xyz(
            inp_text,
            locked["charge"],
            locked["mult"],
            locked["geom"],
            label
        )

        for required_mult in locked["soc_mults"]:
            if str(required_mult) not in inp_text:
                raise RuntimeError(
                    f"{label}: required SOC multiplicity {required_mult} is missing."
                )

        return inp_text

    if policy == "freq_restricted":
        require_text_in_bang(inp_text, locked["method"], label)
        require_text_in_bang(inp_text, locked["basis"], label)
        require_keyword_in_bang(inp_text, "SurfCrossNumFreq", label)
        require_exact_xyz(
            inp_text,
            locked["charge"],
            locked["mult"],
            locked["geom"],
            label
        )
        require_mecp_mult(inp_text, locked["mecp_mult"], label)
        return inp_text

    raise RuntimeError(f"Unknown ORCA repair policy: {policy}")


def edit_failed_input_with_policy(inp_text, job_label, repair_policy="free_full", locked=None):
    locked = locked or {}

    section("ORCA INPUT REVISION REQUIRED")
    print("WARNING: ORCA did not terminate normally.")
    print(f"Failed calculation: {job_label}\n")

    if repair_policy == "interp_header_only":
        pre_xyz, xyz_block, _ = split_orca_xyz_block(inp_text)

        print("Only the pre-geometry ORCA settings block is editable for interpolation jobs.")
        print("The geometry, charge, and multiplicity are protected because interpolation")
        print("uses automatically generated geometries.\n")
        print("Current editable block:\n")
        print(pre_xyz)
        print("\nReview the ORCA 5.0.4 manual and modify convergence settings only.")
        print("Paste the corrected pre-geometry block and finish with EOF.\n")

        edited_pre = collect_multiline_until_eof().rstrip() + "\n"
        repaired = edited_pre + "\n" + xyz_block

    else:
        print("Current input is shown below.")
        print("Paste the corrected input and finish with EOF.\n")
        print(inp_text)
        repaired = collect_multiline_until_eof()

    while True:
        try:
            repaired = validate_repaired_input(
                repaired,
                repair_policy,
                locked,
                job_label
            )
            return repaired

        except RuntimeError as err:
            print("\nINPUT VALIDATION FAILED:")
            print(err)
            print("\nPlease edit again. Finish with EOF.\n")

            if repair_policy == "interp_header_only":
                repaired_pre = collect_multiline_until_eof().rstrip() + "\n"
                repaired = repaired_pre + "\n" + xyz_block
            else:
                repaired = collect_multiline_until_eof()


# ============================================================
# Bash template
# ============================================================

def default_cluster_sh_template():
    return '''#!/bin/bash
#PBS -N {jobname}
#PBS -l nodes=1:ppn={nprocs}
#PBS -o {jobname}.pbs.out
#PBS -e {jobname}.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: {jobname}"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca {inp_filename} > {out_filename}

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
'''


def initialize_cluster_sh_template_once(example_jobname, example_inp, example_out):
    global CLUSTER_SH_TEMPLATE, CLUSTER_SH_TEMPLATE_WAS_REVIEWED, CLUSTER_SH_TEMPLATE_SOURCE

    if globals().get("CLUSTER_SH_TEMPLATE_WAS_REVIEWED", False):
        return

    CLUSTER_SH_TEMPLATE = default_cluster_sh_template()

    example_sh = CLUSTER_SH_TEMPLATE.format(
        jobname=example_jobname,
        nprocs=nprocs,
        inp_filename=example_inp,
        out_filename=example_out
    )

    section("CLUSTER BASH TEMPLATE REVIEW")
    print("The following is the default bash template.\n")
    print("Edit it now if your ORCA path, MPI module, or queue syntax is different.\n")
    print(example_sh)

    if ask_yes_no("Edit this bash template before the first submission?", default=False):
        print("\nPaste the complete edited bash template. Required placeholders:")
        print("  {jobname}, {nprocs}, {inp_filename}, {out_filename}")
        print("Finish with EOF.\n")

        edited = collect_multiline_until_eof()

        validate_placeholders(
            edited,
            ["{jobname}", "{nprocs}", "{inp_filename}", "{out_filename}"],
            "bash"
        )

        CLUSTER_SH_TEMPLATE = edited
        CLUSTER_SH_TEMPLATE_SOURCE = "User-edited in Step 1"

    else:
        CLUSTER_SH_TEMPLATE_SOURCE = "Default ORCA 5.0.4/OpenPBS template from Step 1"

    CLUSTER_SH_TEMPLATE_WAS_REVIEWED = True
    print("\nCluster bash template finalized and will be reused.\n")


def render_cluster_sh(jobname_i, inp_filename_i, out_filename_i):
    return CLUSTER_SH_TEMPLATE.format(
        jobname=jobname_i,
        nprocs=nprocs,
        inp_filename=inp_filename_i,
        out_filename=out_filename_i
    )


def sanitize_submission_prefix(command_text):
    """
    Keep only the queue-submission prefix.

    The generated bash-file name is appended automatically later, so the
    user should not provide the .sh file name here.
    """

    cmd = command_text.strip()

    cmd = cmd.replace("{sh_filename}", "").strip()
    cmd = re.sub(r"\S+\.sh\b", "", cmd).strip()

    cmd = re.sub(r"-l\s+nodes=\d+", "-l nodes=1", cmd)
    cmd = re.sub(r"--nodes[=\s]+\d+", "--nodes=1", cmd)
    cmd = re.sub(r"-N\s+\d+", "-N 1", cmd)

    cmd = re.sub(r"ppn=\d+", f"ppn={nprocs}", cmd)
    cmd = re.sub(r"--ntasks-per-node[=\s]+\d+", f"--ntasks-per-node={nprocs}", cmd)
    cmd = re.sub(r"--ntasks[=\s]+\d+", f"--ntasks={nprocs}", cmd)
    cmd = re.sub(r"-n\s+\d+", f"-n {nprocs}", cmd)
    cmd = re.sub(r"-pe\s+mpi\s+\d+", f"-pe mpi {nprocs}", cmd)

    return " ".join(cmd.split())


def initialize_submit_command_template_once():
    global CLUSTER_SUBMIT_COMMAND_PREFIX
    global CLUSTER_SUBMIT_COMMAND_TEMPLATE
    global CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED
    global CLUSTER_SUBMIT_COMMAND_TEMPLATE_SOURCE

    if globals().get("CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED", False):
        return

    default_prefix = f"qsub -l nodes=1:ppn={nprocs}"

    section("CLUSTER SUBMISSION COMMAND REVIEW")

    print("The default job-submission command prefix is:\n")
    print(f"  {default_prefix}\n")
    print("For each job, the code automatically appends the generated bash-file name.")
    print("Do not paste the bash-file name. The code appends it automatically.\n")

    if ask_yes_no("Use a different submission-command prefix?", default=False):
        print("\nPaste only the submission-command prefix.")
        print("Do not include the .sh file name.")
        print("Finish with EOF.\n")

        edited = collect_multiline_until_eof().strip()
        edited = sanitize_submission_prefix(edited)

        if not edited:
            raise RuntimeError("The submission-command prefix cannot be empty.")

        CLUSTER_SUBMIT_COMMAND_PREFIX = edited
        CLUSTER_SUBMIT_COMMAND_TEMPLATE_SOURCE = "User-edited in Step 1"

    else:
        CLUSTER_SUBMIT_COMMAND_PREFIX = default_prefix
        CLUSTER_SUBMIT_COMMAND_TEMPLATE_SOURCE = "Default qsub submission prefix from Step 1"

    CLUSTER_SUBMIT_COMMAND_PREFIX = sanitize_submission_prefix(
        CLUSTER_SUBMIT_COMMAND_PREFIX
    )

    CLUSTER_SUBMIT_COMMAND_TEMPLATE = (
        CLUSTER_SUBMIT_COMMAND_PREFIX + " {sh_filename}"
    )

    CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = True

    print("\nCluster submission command finalized and will be reused.")
    print("Final submission-command prefix:")
    print(f"  {CLUSTER_SUBMIT_COMMAND_PREFIX}")
    print("Generated bash-file names will be appended automatically.\n")


def render_submit_command(sh_filename_i):
    initialize_submit_command_template_once()
    return f"{CLUSTER_SUBMIT_COMMAND_PREFIX} {sh_filename_i}"


def ensure_cluster_templates_initialized():
    global CLUSTER_SH_TEMPLATE, CLUSTER_SH_TEMPLATE_SOURCE, CLUSTER_SH_TEMPLATE_WAS_REVIEWED
    global CLUSTER_SUBMIT_COMMAND_TEMPLATE, CLUSTER_SUBMIT_COMMAND_TEMPLATE_SOURCE
    global CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED

    if "CLUSTER_SH_TEMPLATE" not in globals():
        CLUSTER_SH_TEMPLATE = default_cluster_sh_template()
        CLUSTER_SH_TEMPLATE_SOURCE = "Default ORCA 5.0.4/OpenPBS template from Step 1"
        CLUSTER_SH_TEMPLATE_WAS_REVIEWED = False

    if "CLUSTER_SUBMIT_COMMAND_TEMPLATE" not in globals():
        CLUSTER_SUBMIT_COMMAND_TEMPLATE = "qsub {sh_filename}"
        CLUSTER_SUBMIT_COMMAND_TEMPLATE_SOURCE = "Default qsub submission command from Step 1"
        CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = False


# ============================================================
# Job submission
# ============================================================

def build_and_upload_job(remote_dir, jobname_i, inp_text):
    inp_filename = f"{jobname_i}.inp"
    out_filename = f"{jobname_i}.out"
    sh_filename = f"{jobname_i}.sh"

    initialize_cluster_sh_template_once(jobname_i, inp_filename, out_filename)

    sh_text = render_cluster_sh(jobname_i, inp_filename, out_filename)

    local_tmp = os.path.abspath(f"cluster_step1_tmp_{jobname}")
    os.makedirs(local_tmp, exist_ok=True)

    local_inp = os.path.join(local_tmp, inp_filename)
    local_sh = os.path.join(local_tmp, sh_filename)

    with open(local_inp, "w", encoding="utf-8", newline="\n") as f:
        f.write(inp_text)

    with open(local_sh, "w", encoding="utf-8", newline="\n") as f:
        f.write(sh_text)

    remote_inp = posixpath.join(remote_dir, inp_filename)
    remote_sh = posixpath.join(remote_dir, sh_filename)

    reconnect_ssh_sftp()
    sftp.put(local_inp, remote_inp)
    sftp.put(local_sh, remote_sh)
    sftp.chmod(remote_sh, 0o755)

    return {
        "inp_filename": inp_filename,
        "out_filename": out_filename,
        "sh_filename": sh_filename,
        "remote_inp": remote_inp,
        "remote_sh": remote_sh
    }


def submit_orca_job_no_monitor(
    remote_dir,
    jobname_i,
    inp_text,
    job_label,
    attempt=1,
    archive_existing_failed=True
):
    status_before = remote_out_status(ssh, remote_dir, jobname_i)

    if status_before == "OK":
        print(f"Completed output already exists for {job_label}. Skipping submission.")
        return {
            "jobname": jobname_i,
            "job_label": job_label,
            "status": "OK",
            "short_id": "",
            "inp_text": inp_text,
            "attempt": attempt
        }

    if archive_existing_failed and status_before in ("ERROR", "FAILED"):
        print(f"Existing non-normal output detected for {job_label}. Archiving old files.")
        archive_remote_job_files(ssh, remote_dir, jobname_i, "previous_failed")

    files = build_and_upload_job(remote_dir, jobname_i, inp_text)
    submit_cmd = render_submit_command(files["sh_filename"])

    section(f"SUBMITTING JOB | {job_label}")
    print(f"Remote directory  : {remote_dir}")
    print(f"Attempt           : {attempt}")
    print(f"Submission command: {submit_cmd}")

    qsub_out, qsub_err = run_ssh(
        ssh,
        f'cd "{remote_dir}" && {submit_cmd}'
    )

    short_id = ""

    if qsub_out:
        print(f"Queue response    : {qsub_out}")
        short_id = qsub_out.split()[0].split(".")[0]
    elif qsub_err:
        print("Queue-system message:")
        print(qsub_err)

    return {
        "jobname": jobname_i,
        "job_label": job_label,
        "status": "SUBMITTED",
        "short_id": short_id,
        "inp_text": inp_text,
        "attempt": attempt
    }


def _resubmit_same_input_after_missing_output(remote_dir, job):
    """
    Used when PBS job is E/not in qstat but no .out file exists.
    This is treated as a cluster/submission issue, not an ORCA-input failure.
    """
    new_attempt = int(job.get("attempt", 1)) + 1

    print(
        f"\nWARNING: {job['job_label']} is not active in qstat and "
        f"{job['jobname']}.out is missing."
    )
    print("Treating this as a cluster/submission issue and resubmitting the same input.")
    print(f"Resubmission attempt: {new_attempt}")

    new_job = submit_orca_job_no_monitor(
        remote_dir,
        job["jobname"],
        job["inp_text"],
        job["job_label"],
        attempt=new_attempt,
        archive_existing_failed=False
    )

    for key, value in job.items():
        if key not in new_job:
            new_job[key] = value

    new_job["attempt"] = new_attempt
    new_job["inp_text"] = job["inp_text"]
    new_job["repair_policy"] = job.get("repair_policy", "free_full")
    new_job["locked"] = job.get("locked", {})

    return new_job


def submit_and_monitor_orca_job(
    remote_dir,
    jobname_i,
    inp_text,
    job_label,
    allow_interactive_repair=True,
    repair_policy="free_full",
    locked=None
):
    current_inp = inp_text
    attempt = 1
    locked = locked or {}

    max_missing_resubmits = 50
    missing_resubmits = 0

    while True:
        status_before = remote_out_status(ssh, remote_dir, jobname_i)

        if status_before == "OK":
            print(f"Completed output already exists for {job_label}. Skipping.")
            return "OK", current_inp

        if status_before in ("ERROR", "FAILED"):
            print(f"Existing non-normal output detected for {job_label}. Archiving old files.")
            archive_remote_job_files(ssh, remote_dir, jobname_i, "previous_failed")

        files = build_and_upload_job(remote_dir, jobname_i, current_inp)
        submit_cmd = render_submit_command(files["sh_filename"])

        section(f"SUBMITTING JOB | {job_label}")
        print(f"Remote directory  : {remote_dir}")
        print(f"Attempt           : {attempt}")
        print(f"Submission command: {submit_cmd}")

        qsub_out, qsub_err = run_ssh(
            ssh,
            f'cd "{remote_dir}" && {submit_cmd}'
        )

        short_id = ""

        if qsub_out:
            print(f"Queue response    : {qsub_out}")
            short_id = qsub_out.split()[0].split(".")[0]
        elif qsub_err:
            print("Queue-system message:")
            print(qsub_err)

        terminal_status = None

        for cycle in range(100000):
            time.sleep(20)

            qstat_out, _ = run_ssh(ssh, "qstat -u $USER")
            state = get_job_state(qstat_out, short_id)
            now = remote_out_status(ssh, remote_dir, jobname_i)

            if now == "OK":
                print(f"{job_label} completed successfully.")
                return "OK", current_inp

            if state in ("Q", "R", "H", "S"):
                if cycle % 15 == 0:
                    print(f"{job_label} is active. PBS state = {state}")
                continue

            # PBS E or missing from qstat means: job ended or vanished.
            # Decide only from the ORCA output file.
            if state in ("E", None):

                if now == "OK":
                    print(f"{job_label} completed successfully.")
                    return "OK", current_inp

                if now in ("ERROR", "FAILED"):
                    terminal_status = now
                    break

                if now == "MISSING":
                    if missing_resubmits >= max_missing_resubmits:
                        terminal_status = "MISSING"
                        break

                    missing_resubmits += 1
                    attempt += 1

                    print(
                        f"\nWARNING: {job_label} is not active in qstat and "
                        f"{jobname_i}.out is missing."
                    )
                    print("Treating this as a cluster/submission issue and resubmitting the same input.")
                    print(f"Missing-output resubmission {missing_resubmits}/{max_missing_resubmits}")

                    files = build_and_upload_job(remote_dir, jobname_i, current_inp)
                    submit_cmd = render_submit_command(files["sh_filename"])

                    qsub_out, qsub_err = run_ssh(
                        ssh,
                        f'cd "{remote_dir}" && {submit_cmd}'
                    )

                    short_id = ""

                    if qsub_out:
                        print(f"Queue response    : {qsub_out}")
                        short_id = qsub_out.split()[0].split(".")[0]
                    elif qsub_err:
                        print("Queue-system message:")
                        print(qsub_err)

                    continue

            if cycle % 15 == 0:
                print(
                    f"Waiting for {job_label}. "
                    f"PBS state = {state}, ORCA output status = {now}"
                )

        if terminal_status is None:
            return "TIMEOUT", current_inp

        if terminal_status == "MISSING":
            section(f"MISSING OUTPUT AFTER RESUBMISSIONS | {job_label}")
            print(f"No {jobname_i}.out file appeared after repeated resubmissions.")
            print("This is likely a cluster/submission/environment issue.")
            return "MISSING", current_inp

        section(f"NON-NORMAL TERMINATION | {job_label}")
        print("WARNING: ORCA did not terminate normally.")
        print(f"Detected status: {terminal_status}")
        print(f"Remote output  : {remote_dir}/{jobname_i}.out")

        tail_text = remote_tail(ssh, remote_dir, f"{jobname_i}.out", 100)

        if tail_text.strip():
            subsection("Last 100 lines of output")
            print(tail_text)

        if not allow_interactive_repair:
            return terminal_status, current_inp

        if not ask_yes_no("Edit this ORCA input and resubmit?", default=True):
            return terminal_status, current_inp

        current_inp = edit_failed_input_with_policy(
            current_inp,
            job_label,
            repair_policy=repair_policy,
            locked=locked
        )

        archive_remote_job_files(
            ssh,
            remote_dir,
            jobname_i,
            "failed_attempt"
        )

        attempt += 1
        missing_resubmits = 0


def monitor_submitted_orca_jobs(
    remote_dir,
    submitted_jobs,
    allow_interactive_repair=True
):
    unfinished = {
        job["jobname"]: job
        for job in submitted_jobs
        if job["status"] != "OK"
    }

    max_missing_resubmits = 50

    while unfinished:
        time.sleep(20)
        qstat_out, _ = run_ssh(ssh, "qstat -u $USER")

        for jobname_i in list(unfinished.keys()):
            job = unfinished[jobname_i]
            state = get_job_state(qstat_out, job["short_id"])
            status_now = remote_out_status(ssh, remote_dir, jobname_i)

            if status_now == "OK":
                print(f"{job['job_label']} completed successfully.")
                job["status"] = "OK"
                unfinished.pop(jobname_i)
                continue

            if state in ("Q", "R", "H", "S"):
                print(f"{job['job_label']} is active. PBS state = {state}")
                continue

            # PBS E or missing from qstat means: job ended or vanished.
            # Decide based on ORCA output status.
            if state in ("E", None):

                if status_now == "OK":
                    print(f"{job['job_label']} completed successfully.")
                    job["status"] = "OK"
                    unfinished.pop(jobname_i)
                    continue

                if status_now == "MISSING":
                    missing_count = int(job.get("missing_resubmits", 0))

                    if missing_count >= max_missing_resubmits:
                        section(f"MISSING OUTPUT AFTER RESUBMISSIONS | {job['job_label']}")
                        print(f"No {jobname_i}.out file appeared after repeated resubmissions.")
                        print("This is likely a cluster/submission/environment issue.")
                        job["status"] = "MISSING"
                        unfinished.pop(jobname_i)
                        continue

                    job["missing_resubmits"] = missing_count + 1

                    new_job = _resubmit_same_input_after_missing_output(
                        remote_dir,
                        job
                    )

                    new_job["missing_resubmits"] = job["missing_resubmits"]

                    unfinished[jobname_i] = new_job

                    for k, old_job in enumerate(submitted_jobs):
                        if old_job["jobname"] == jobname_i:
                            submitted_jobs[k] = new_job
                            break

                    continue

                if status_now in ("ERROR", "FAILED"):
                    section(f"NON-NORMAL TERMINATION | {job['job_label']}")
                    print("WARNING: ORCA did not terminate normally.")
                    print(f"Detected status: {status_now}")
                    print(f"Remote output  : {remote_dir}/{jobname_i}.out")

                    tail_text = remote_tail(
                        ssh,
                        remote_dir,
                        f"{jobname_i}.out",
                        100
                    )

                    if tail_text.strip():
                        subsection("Last 100 lines of output")
                        print(tail_text)

                    if not allow_interactive_repair:
                        job["status"] = status_now
                        unfinished.pop(jobname_i)
                        continue

                    if not ask_yes_no("Edit this ORCA input and resubmit?", default=True):
                        job["status"] = status_now
                        unfinished.pop(jobname_i)
                        continue

                    corrected_inp = edit_failed_input_with_policy(
                        job["inp_text"],
                        job["job_label"],
                        repair_policy=job.get("repair_policy", "free_full"),
                        locked=job.get("locked", {})
                    )

                    archive_remote_job_files(
                        ssh,
                        remote_dir,
                        jobname_i,
                        "failed_attempt"
                    )

                    new_attempt = int(job.get("attempt", 1)) + 1

                    new_job = submit_orca_job_no_monitor(
                        remote_dir,
                        jobname_i,
                        corrected_inp,
                        job["job_label"],
                        attempt=new_attempt
                    )

                    for key, value in job.items():
                        if key not in new_job:
                            new_job[key] = value

                    new_job["inp_text"] = corrected_inp
                    new_job["attempt"] = new_attempt
                    new_job["repair_policy"] = job.get("repair_policy", "free_full")
                    new_job["locked"] = job.get("locked", {})
                    new_job["missing_resubmits"] = 0

                    unfinished[jobname_i] = new_job

                    for k, old_job in enumerate(submitted_jobs):
                        if old_job["jobname"] == jobname_i:
                            submitted_jobs[k] = new_job
                            break

                    continue

            print(
                f"Waiting for {job['job_label']}. "
                f"PBS state = {state}, ORCA output status = {status_now}"
            )

    return submitted_jobs


# ============================================================
# Cluster connection and user input
# ============================================================

section("CLUSTER CONNECTION")

cluster_host = safe_input("Cluster hostname: ").strip()

while not cluster_host:
    cluster_host = safe_input("Cluster hostname: ").strip()

cluster_account = safe_input("Cluster username: ").strip() or getpass.getuser()
cluster_password = (
    safe_input("Cluster password: ")
    if IS_SPYDER
    else getpass.getpass("Cluster password: ")
)

print("\nConnecting to the cluster...")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    cluster_host,
    username=cluster_account,
    password=cluster_password,
    timeout=30
)

sftp = ssh.open_sftp()

remote_home, _ = run_ssh(ssh, "echo $HOME")
remote_home = remote_home.strip()

if not remote_home:
    raise RuntimeError("The remote $HOME directory could not be determined.")

print("Cluster connection established.")
print(f"Remote HOME directory: {remote_home}")


section("MOLECULAR AND COMPUTATIONAL INPUT")

jobname = safe_input(
    "Please provide a name for your input file without extension, e.g. Iron_cluster: "
).strip()

while not jobname:
    jobname = safe_input(
        "Project/job name without extension, e.g. Paper_2: "
    ).strip()

multiplicities = parse_int_list(
    safe_input(
        "\nEnter the two spin multiplicities, e.g. 3,5 or 4,6, etc: "
    ).strip()
)

if len(multiplicities) != 2:
    raise RuntimeError(
        "This workflow expects exactly two multiplicities, for example 3,5."
    )

charge = ask_int("Charge of the species, e.g. -2 or 0 or 3, etc: ")

default_method = "PBE"
default_basis = "def2-TZVP"

print("\nDefault Step 1 electronic-structure settings:")
print(f"  Method/level of theory : {default_method}")
print(f"  Basis set              : {default_basis}")
print("These values are used to build the initial DFT header.")
print("You can still edit the generated pre-geometry ORCA settings block once before submission.\n")

method = ask_nonempty_text(
    f"Level of theory / method for DFT jobs, e.g. B3LYP D3 or MP2 (default {default_method}): ",
    default=default_method
)

basis = ask_nonempty_text(
    f"Basis set for all jobs, e.g. def2-TZVP or 6-31G(d) (default {default_basis}): ",
    default=default_basis
)

nprocs = ask_int("\nNumber of ORCA processors/cores, e.g. 12: ", minimum=1)
mem_gb = ask_int("ORCA %maxcore memory per core in GB, e.g. 8: ", minimum=1)
maxcore_mb = mem_gb * 1000

n_interp = 10

Initial_geometry = read_geometry_block(
    "Paste the initial guess Cartesian geometry. This geometry is used to optimize the spin-state minima for your requested multiplicities. It is protected and cannot be edited during ORCA input-file editing."
)

ORCA_INPUT_TEMPLATES = {
    "minimum": default_orca_template("minimum"),
    "single_point": default_orca_template("single_point"),
    "mecp": default_orca_template("mecp"),
}

ORCA_INPUT_TEMPLATES_WERE_REVIEWED = True
ORCA_INPUT_TEMPLATE_SOURCE = "Default PBE/def2-TZVP Step 1 templates; minimum inputs reviewed once individually"


# ============================================================
# Directories
# ============================================================

section("DIRECTORY PREPARATION")

remote_base = posixpath.join(remote_home, f"Spin_Inversion_{jobname}")
remote_minima = posixpath.join(remote_base, "Minima")
remote_interp = posixpath.join(remote_base, "Interpolation_scan")
remote_mecp = posixpath.join(remote_base, "MECP")

for d in (remote_base, remote_minima, remote_interp, remote_mecp):
    remote_mkdir_p(sftp, d)

local_base = os.path.abspath(f"Spin_Inversion_{jobname}_cluster_local")
local_minima = os.path.join(local_base, "Minima")
local_interp = os.path.join(local_base, "Interpolation_scan")
local_mecp = os.path.join(local_base, "MECP")

for d in (local_base, local_minima, local_interp, local_mecp):
    os.makedirs(d, exist_ok=True)

print(f"Remote base directory          : {remote_base}")
print(f"Remote minima directory        : {remote_minima}")
print(f"Remote interpolation directory : {remote_interp}")
print(f"Remote MECP directory          : {remote_mecp}")
print(f"Local metadata directory       : {local_base}")

low_spin_mult = min(multiplicities)
high_spin_mult = max(multiplicities)

# Minimum inputs are reviewed once below in MINIMUM INPUT FILE REVIEW.
# The separate template-review prompt is intentionally not called here,
# so the user is not asked twice for low-spin/high-spin input edits.


# ============================================================
# Minima input review
# ============================================================

section("MINIMUM INPUT FILE REVIEW")

minima_inputs = {}

low_spin_mult = min(multiplicities)
high_spin_mult = max(multiplicities)

for mult in [low_spin_mult, high_spin_mult]:
    spin_name = multiplicity_name(mult)
    job_i = f"{jobname}_{spin_name}_min"
    inp_text = ORCA_INPUT_TEMPLATES["minimum"].format(
        method=method,
        basis=basis,
        charge=charge,
        mult=mult,
        mult_other=high_spin_mult if mult == low_spin_mult else low_spin_mult,
        geom=Initial_geometry,
        nprocs=nprocs,
        maxcore_mb=maxcore_mb
    )
    minima_inputs[mult] = {
        "spin_name": spin_name,
        "jobname": job_i,
        "inp_text": inp_text
    }

print("\nThe two complete minimum input files generated from your method/basis are shown below.")
print("The geometry, charge, and multiplicity are protected. If you edit, you will edit only")
print("the ORCA settings block before the * xyz line; that same settings block will be")
print("propagated to interpolation, MECP, Engrad, and frequency-style DFT jobs.\n")

for mult in [low_spin_mult, high_spin_mult]:
    job_i = minima_inputs[mult]["jobname"]
    existing_status = remote_out_status(ssh, remote_minima, job_i)
    subsection(f"Complete generated minimum input | {job_i}.inp")
    print(minima_inputs[mult]["inp_text"])
    if existing_status == "OK":
        print(f"Completed minimum output already exists for {job_i}; this job will be skipped if submitted.")

default_pre_xyz, _, _ = split_orca_xyz_block(minima_inputs[low_spin_mult]["inp_text"])
reviewed_pre_xyz = default_pre_xyz

if ask_yes_no("Edit the shared ORCA settings block before minimum submissions?", default=False):
    section("EDIT SHARED PRE-GEOMETRY ORCA SETTINGS")
    print("Edit only the block below. Do NOT include the * xyz line, charge, multiplicity,")
    print("geometry, or final '*' line. The edited block will be applied to both minimum")
    print("inputs with their correct protected charge/multiplicity/geometry.\n")
    print("Current shared editable block:\n")
    print(default_pre_xyz)
    print("\nPaste the corrected pre-geometry block and finish with EOF.\n")
    reviewed_pre_xyz = collect_multiline_until_eof()
    if "* xyz" in reviewed_pre_xyz.lower():
        raise RuntimeError("The edited shared settings block must not contain a '* xyz' geometry block.")
    if not first_bang_line(reviewed_pre_xyz):
        raise RuntimeError("The edited shared settings block must contain an ORCA ! line.")

DFT_REVIEWED_PRE_XYZ = reviewed_pre_xyz.rstrip() + "\n"
ORCA_TRANSFER_BLOCKS_ALL = extract_orca_percent_blocks_from_pre(DFT_REVIEWED_PRE_XYZ, exclude_names=[])
ORCA_TRANSFER_BLOCKS_NO_OUTPUT = extract_orca_percent_blocks_from_pre(DFT_REVIEWED_PRE_XYZ, exclude_names=["output"])

for mult in [low_spin_mult, high_spin_mult]:
    final_min_inp = build_orca_input_from_pre_xyz(make_dft_pre_xyz("minimum"), charge, mult, Initial_geometry)
    final_min_inp = enforce_minimum_input_requirements(final_min_inp, minima_inputs[mult]["jobname"])
    minima_inputs[mult]["inp_text"] = final_min_inp

print("\nMinimum input files finalized.")
print("The reviewed DFT header and all % blocks have been captured for propagation.")
print("Interpolation and MECP input files will be generated automatically.\n")


# ============================================================
# Minima
# ============================================================

section("MINIMUM OPTIMIZATIONS")

minima_data = {}
submitted_minima_jobs = []

for mult in multiplicities:
    spin_name = minima_inputs[mult]["spin_name"]
    job_i = minima_inputs[mult]["jobname"]
    inp_text = minima_inputs[mult]["inp_text"]

    job_record = submit_orca_job_no_monitor(
        remote_minima,
        job_i,
        inp_text,
        f"{spin_name} minimum Opt/Freq",
        attempt=1
    )

    job_record["repair_policy"] = "free_minimum"
    job_record["locked"] = {}

    submitted_minima_jobs.append(job_record)

print("\nBoth spin-state minimum jobs have been submitted.")
print("The two calculations will now be monitored simultaneously.\n")

submitted_minima_jobs = monitor_submitted_orca_jobs(
    remote_minima,
    submitted_minima_jobs,
    allow_interactive_repair=True
)

for job_record in submitted_minima_jobs:
    if job_record["status"] != "OK":
        raise SystemExit(
            f"{job_record['jobname']} did not complete successfully."
        )

for mult in multiplicities:
    spin_name = minima_inputs[mult]["spin_name"]
    job_i = minima_inputs[mult]["jobname"]

    matching_jobs = [
        job for job in submitted_minima_jobs
        if job["jobname"] == job_i
    ]

    final_inp = (
        matching_jobs[0]["inp_text"]
        if matching_jobs
        else minima_inputs[mult]["inp_text"]
    )

    remote_out = posixpath.join(remote_minima, f"{job_i}.out")
    remote_xyz = posixpath.join(remote_minima, f"{job_i}.xyz")

    if not remote_file_exists(sftp, remote_xyz):
        raise RuntimeError(
            f"Optimized XYZ file was not found remotely:\n{remote_xyz}"
        )

    out_text = remote_read_text(sftp, remote_out)
    xyz_text = remote_read_text(sftp, remote_xyz)

    ele, zpe = extract_ele_zpe_from_text(out_text, remote_out)
    geom = extract_geometry_from_xyz_text(xyz_text, remote_xyz)

    for fname, text in [
        (f"{job_i}.out", out_text),
        (f"{job_i}.xyz", xyz_text),
        (f"{job_i}.inp", final_inp)
    ]:
        with open(
            os.path.join(local_minima, fname),
            "w",
            encoding="utf-8",
            newline="\n"
        ) as f:
            f.write(text)

    minima_data[mult] = {
        "spin_name": spin_name,
        "jobname": job_i,
        "out_path": remote_out,
        "xyz_path": remote_xyz,
        "Electronic_Eh": ele,
        "ZPE_Eh": zpe,
        "E_plus_ZPE_Eh": ele + zpe,
        "geometry": geom
    }


# ============================================================
# Reference selection
# ============================================================

section("MINIMA ENERGY SUMMARY AND REFERENCE SELECTION")

hartree_to_cm = 219474.6
hartree_to_kcal = 627.509474

sorted_mults = sorted(
    multiplicities,
    key=lambda m: minima_data[m]["E_plus_ZPE_Eh"]
)

reference_multiplicity = sorted_mults[0]
other_multiplicity = sorted_mults[1]

Reference_multiplicity = reference_multiplicity
Reference_geometry = minima_data[reference_multiplicity]["geometry"]

Reference_source = (
    f"Automatically selected lower electronic-plus-ZPE minimum: multiplicity "
    f"{Reference_multiplicity} "
    f"({minima_data[Reference_multiplicity]['spin_name']})"
)

E0 = minima_data[reference_multiplicity]["E_plus_ZPE_Eh"]

for mult in multiplicities:
    dEh = minima_data[mult]["E_plus_ZPE_Eh"] - E0

    print(f"Multiplicity {mult} ({minima_data[mult]['spin_name']}):")
    print(f"  Electronic energy       = {minima_data[mult]['Electronic_Eh']:.12f} Eh")
    print(f"  Zero-point energy       = {minima_data[mult]['ZPE_Eh']:.12f} Eh")
    print(f"  Electronic + ZPE        = {minima_data[mult]['E_plus_ZPE_Eh']:.12f} Eh")
    print(f"  Relative E+ZPE          = {dEh * hartree_to_cm:.6f} cm^-1")
    print(f"  Relative E+ZPE          = {dEh * hartree_to_kcal:.6f} kcal/mol\n")

print(f"Reference multiplicity: {Reference_multiplicity}")


# ============================================================
# Interpolation scan
# ============================================================

section("CARTESIAN INTERPOLATION SCAN")

mult_a, mult_b = multiplicities

geom_a = minima_data[mult_a]["geometry"]
geom_b = minima_data[mult_b]["geometry"]

interp_records = []
interp_batch_geometries = 2

interp_jobs_by_index = {}

for i in range(n_interp):
    lam = i / (n_interp - 1)
    geom_i = interpolate_geometry(geom_a, geom_b, lam)

    symbols, coords = parse_geometry_to_arrays(geom_i)

    local_xyz = os.path.join(
        local_interp,
        f"{jobname}_interp_{i+1:03d}_lambda_{lam:.4f}.xyz"
    )

    with open(local_xyz, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{len(symbols)}\n")
        f.write(f"lambda = {lam:.8f}\n")
        f.write(geom_i + "\n")

    remote_xyz = posixpath.join(remote_interp, os.path.basename(local_xyz))
    sftp.put(local_xyz, remote_xyz)

    interp_jobs_by_index[i + 1] = {
        "index": i + 1,
        "lambda": lam,
        "geometry": geom_i,
        "geometry_file": remote_xyz,
        "local_geometry_file": local_xyz,
        "jobs": []
    }

    for mult in multiplicities:
        spin_name = multiplicity_name(mult)

        sp_job = (
            f"{jobname}_interp_{i+1:03d}_lam_{lam:.4f}_"
            f"mult_{mult}_{spin_name}"
        )

        sp_inp = ORCA_INPUT_TEMPLATES["single_point"].format(
            method=method,
            basis=basis,
            charge=charge,
            mult=mult,
            mult_other=multiplicities[1] if mult == multiplicities[0] else multiplicities[0],
            geom=geom_i,
            nprocs=nprocs,
            maxcore_mb=maxcore_mb
        )
        sp_inp = apply_dft_settings_to_input(
            sp_inp,
            job_type="single_point"
        )

        interp_jobs_by_index[i + 1]["jobs"].append({
            "mult": mult,
            "spin_name": spin_name,
            "jobname": sp_job,
            "inp_text": sp_inp,
            "label": f"interpolation SP {i+1:03d}, mult {mult}"
        })


for batch_start in range(1, n_interp + 1, interp_batch_geometries):
    batch_indices = list(
        range(
            batch_start,
            min(batch_start + interp_batch_geometries, n_interp + 1)
        )
    )

    section(
        "INTERPOLATION BATCH "
        f"{batch_indices[0]:03d}-{batch_indices[-1]:03d}"
    )

    submitted_interp_jobs = []

    for idx in batch_indices:
        for job in interp_jobs_by_index[idx]["jobs"]:
            job_record = submit_orca_job_no_monitor(
                remote_interp,
                job["jobname"],
                job["inp_text"],
                job["label"],
                attempt=1
            )

            job_record["interp_index"] = idx
            job_record["mult"] = job["mult"]
            job_record["spin_name"] = job["spin_name"]
            job_record["repair_policy"] = "interp_header_only"
            job_record["locked"] = {
                "method": method,
                "basis": basis,
            }

            submitted_interp_jobs.append(job_record)

    print(
        f"\nSubmitted {len(submitted_interp_jobs)} interpolation single-point jobs "
        f"for geometries {batch_indices[0]:03d}-{batch_indices[-1]:03d}."
    )
    print("These jobs will now be monitored simultaneously.\n")

    submitted_interp_jobs = monitor_submitted_orca_jobs(
        remote_interp,
        submitted_interp_jobs,
        allow_interactive_repair=True
    )

    for job_record in submitted_interp_jobs:
        if job_record["status"] != "OK":
            raise SystemExit(
                f"{job_record['jobname']} did not complete successfully."
            )

    for idx in batch_indices:
        energies = {}

        for job_record in submitted_interp_jobs:
            if job_record["interp_index"] != idx:
                continue

            mult = job_record["mult"]
            sp_job = job_record["jobname"]

            out_text = remote_read_text(
                sftp,
                posixpath.join(remote_interp, f"{sp_job}.out")
            )

            energies[mult] = extract_final_sp_energy_from_text(
                out_text,
                sp_job
            )

        if mult_a not in energies or mult_b not in energies:
            raise RuntimeError(
                f"Missing one or more interpolation energies for point {idx:03d}."
            )

        lam = interp_jobs_by_index[idx]["lambda"]
        geom_i = interp_jobs_by_index[idx]["geometry"]
        remote_xyz = interp_jobs_by_index[idx]["geometry_file"]
        local_xyz = interp_jobs_by_index[idx]["local_geometry_file"]

        gap = energies[mult_a] - energies[mult_b]

        record = {
            "index": idx,
            "lambda": lam,
            "geometry": geom_i,
            "geometry_file": remote_xyz,
            "local_geometry_file": local_xyz,
            "energies_Eh": energies,
            "gap_Eh": gap,
            "gap_abs_Eh": abs(gap),
            "gap_cm1": gap * hartree_to_cm,
            "gap_abs_cm1": abs(gap * hartree_to_cm),
            "gap_kcal": gap * hartree_to_kcal
        }

        interp_records.append(record)
        interp_records = sorted(interp_records, key=lambda r: r["index"])

        print(f"Interpolation point {idx:03d}/{n_interp}")
        print(f"  lambda                         = {lam:.6f}")
        print(f"  E(mult {mult_a})                = {energies[mult_a]:.12f} Eh")
        print(f"  E(mult {mult_b})                = {energies[mult_b]:.12f} Eh")
        print(f"  signed gap E{mult_a}-E{mult_b}  = {record['gap_cm1']:.3f} cm^-1")
        print(f"  absolute gap                   = {record['gap_abs_cm1']:.3f} cm^-1\n")


best_record = min(interp_records, key=lambda r: r["gap_abs_Eh"])
MECP_start_geometry = best_record["geometry"]

section("BEST INTERPOLATED MECP STARTING GEOMETRY")

print(f"Best interpolation index             : {best_record['index']}")
print(f"Best lambda                          : {best_record['lambda']:.8f}")
print(f"Absolute spin-state gap              : {best_record['gap_abs_cm1']:.6f} cm^-1")
print("\nMECP starting geometry:\n")
print(MECP_start_geometry)

scan_summary_file = os.path.join(
    local_interp,
    f"{jobname}_interpolation_scan_summary.txt"
)

with open(scan_summary_file, "w", encoding="utf-8", newline="\n") as f:
    f.write("Linear Cartesian interpolation scan\n")
    f.write(f"jobname = {jobname}\n")
    f.write(f"charge = {charge}\n")
    f.write(f"multiplicities = {multiplicities}\n")
    f.write(f"method = {method}\n")
    f.write(f"basis = {basis}\n")
    f.write(f"n_interp = {n_interp}\n\n")
    f.write(
        "index  lambda      E_mult_a_Eh        E_mult_b_Eh        "
        "gap_cm1        abs_gap_cm1      gap_kcal\n"
    )

    for r in interp_records:
        f.write(
            f"{r['index']:5d}  "
            f"{r['lambda']:10.6f}  "
            f"{r['energies_Eh'][mult_a]:18.12f}  "
            f"{r['energies_Eh'][mult_b]:18.12f}  "
            f"{r['gap_cm1']:14.6f}  "
            f"{r['gap_abs_cm1']:14.6f}  "
            f"{r['gap_kcal']:14.6f}\n"
        )

remote_scan_summary_file = posixpath.join(
    remote_interp,
    os.path.basename(scan_summary_file)
)

sftp.put(scan_summary_file, remote_scan_summary_file)

# ============================================================
# Interpolation energy plot
# ============================================================

section("INTERPOLATION ENERGY PLOT")

# Keep interpolation points in geometrical order.
interp_records = sorted(interp_records, key=lambda r: r["index"])

interpolation_indices = np.array(
    [r["index"] for r in interp_records],
    dtype=int
)

# Multiplicity determines low-spin versus high-spin labeling.
low_spin_interp_mult = min(multiplicities)
high_spin_interp_mult = max(multiplicities)

low_spin_energies_Eh = np.array(
    [
        r["energies_Eh"][low_spin_interp_mult]
        for r in interp_records
    ],
    dtype=float
)

high_spin_energies_Eh = np.array(
    [
        r["energies_Eh"][high_spin_interp_mult]
        for r in interp_records
    ],
    dtype=float
)

# The lowest electronic energy found on either interpolated spin surface
# is used as the common zero. The interpolation scan consists of
# electronic single-point calculations, so no ZPE correction is applied.
interpolation_reference_Eh = float(
    min(
        np.min(low_spin_energies_Eh),
        np.min(high_spin_energies_Eh)
    )
)

low_spin_relative_kcal = (
    low_spin_energies_Eh - interpolation_reference_Eh
) * hartree_to_kcal

high_spin_relative_kcal = (
    high_spin_energies_Eh - interpolation_reference_Eh
) * hartree_to_kcal

interpolation_plot_png = os.path.join(
    local_interp,
    f"{jobname}_interpolation_energy_scan.png"
)

interpolation_plot_pdf = os.path.join(
    local_interp,
    f"{jobname}_interpolation_energy_scan.pdf"
)

fig, ax = plt.subplots(figsize=(7.2, 4.8))

ax.plot(
    interpolation_indices,
    low_spin_relative_kcal,
    marker="o",
    linewidth=2.0,
    markersize=5,
    label="Low spin"
)

ax.plot(
    interpolation_indices,
    high_spin_relative_kcal,
    marker="o",
    linewidth=2.0,
    markersize=5,
    label="High spin"
)

ax.set_xlabel("Interpolation geometry", fontsize=12)
ax.set_ylabel("Energy (kcal/mol)", fontsize=12)
ax.set_xticks(interpolation_indices)
ax.legend(frameon=False)

fig.tight_layout()

fig.savefig(
    interpolation_plot_png,
    dpi=600,
    bbox_inches="tight"
)

fig.savefig(
    interpolation_plot_pdf,
    bbox_inches="tight"
)

plt.show()
plt.close(fig)

remote_interpolation_plot_png = posixpath.join(
    remote_interp,
    os.path.basename(interpolation_plot_png)
)

remote_interpolation_plot_pdf = posixpath.join(
    remote_interp,
    os.path.basename(interpolation_plot_pdf)
)

reconnect_ssh_sftp()
sftp.put(interpolation_plot_png, remote_interpolation_plot_png)
sftp.put(interpolation_plot_pdf, remote_interpolation_plot_pdf)

print(f"Low-spin multiplicity             = {low_spin_interp_mult}")
print(f"High-spin multiplicity            = {high_spin_interp_mult}")
print(f"Interpolation reference energy    = {interpolation_reference_Eh:.12f} Eh")
print(f"Local interpolation plot PNG      = {interpolation_plot_png}")
print(f"Local interpolation plot PDF      = {interpolation_plot_pdf}")
print(f"Remote interpolation plot PNG     = {remote_interpolation_plot_png}")
print(f"Remote interpolation plot PDF     = {remote_interpolation_plot_pdf}")

best_start_file = os.path.join(
    local_interp,
    f"{jobname}_best_MECP_crossing_guess.xyz"
)

sym_best, _ = parse_geometry_to_arrays(MECP_start_geometry)

with open(best_start_file, "w", encoding="utf-8", newline="\n") as f:
    f.write(f"{len(sym_best)}\n")
    f.write(
        f"Best interpolated MECP guess: "
        f"index={best_record['index']}, "
        f"lambda={best_record['lambda']:.8f}\n"
    )
    f.write(MECP_start_geometry + "\n")

remote_best_start_file = posixpath.join(
    remote_interp,
    os.path.basename(best_start_file)
)

sftp.put(best_start_file, remote_best_start_file)


# ============================================================
# MECP
# ============================================================

section("MECP OPTIMIZATION")

mult_main = reference_multiplicity
mult_other = other_multiplicity
mecp_jobname = jobname

mecp_inp = ORCA_INPUT_TEMPLATES["mecp"].format(
    method=method,
    basis=basis,
    charge=charge,
    mult=mult_main,
    mult_other=mult_other,
    geom=MECP_start_geometry,
    nprocs=nprocs,
    maxcore_mb=maxcore_mb
)
mecp_job_specific_blocks = f"""%mecp Mult {mult_other}
end
"""

mecp_inp = apply_dft_settings_to_input(
    mecp_inp,
    job_type="mecp",
    job_specific_blocks=mecp_job_specific_blocks
)

status, final_mecp_inp = submit_and_monitor_orca_job(
    remote_mecp,
    mecp_jobname,
    mecp_inp,
    "MECP SurfCrossOpt",
    True,
    repair_policy="mecp_restricted",
    locked={
        "method": method,
        "basis": basis,
        "charge": charge,
        "mult": mult_main,
        "mecp_mult": mult_other,
        "geom": MECP_start_geometry,
    }
)

if status != "OK":
    raise SystemExit("MECP optimization did not complete successfully.")

remote_mecp_xyz = posixpath.join(remote_mecp, f"{mecp_jobname}.xyz")

if not remote_file_exists(sftp, remote_mecp_xyz):
    ls_xyz, _ = run_ssh(
        ssh,
        f'cd "{remote_mecp}" && ls *.xyz 2>/dev/null | head -n 1'
    )

    if not ls_xyz.strip():
        raise RuntimeError(
            "No MECP XYZ file was found after the MECP optimization."
        )

    remote_mecp_xyz = posixpath.join(remote_mecp, ls_xyz.strip())

MECP_geometry = extract_geometry_from_xyz_text(
    remote_read_text(sftp, remote_mecp_xyz),
    remote_mecp_xyz
)

section("EXTRACTED MECP GEOMETRY")
print(MECP_geometry)

ensure_cluster_templates_initialized()


# ============================================================
# Save metadata
# ============================================================

section("SAVING STEP 1 OUTPUTS")

paths_text = {
    os.path.join(local_mecp, f"{jobname}_MECP_geometry.txt"):
        MECP_geometry + "\n",

    os.path.join(local_base, f"{jobname}_Reference_geometry.txt"):
        Reference_geometry + "\n",

    os.path.join(local_mecp, f"{jobname}_MECP_start_interpolated_geometry.txt"):
        MECP_start_geometry + "\n",

    os.path.join(local_base, f"{jobname}_cluster_sh_template.txt"):
        CLUSTER_SH_TEMPLATE,

    os.path.join(local_mecp, f"{jobname}_final_MECP_input.inp"):
        final_mecp_inp,

    os.path.join(local_base, f"{jobname}_DFT_reviewed_pre_xyz_settings.txt"):
        DFT_REVIEWED_PRE_XYZ,

    os.path.join(local_base, f"{jobname}_ORCA_transfer_blocks_all.txt"):
        ORCA_TRANSFER_BLOCKS_ALL,

    os.path.join(local_base, f"{jobname}_ORCA_transfer_blocks_no_output.txt"):
        ORCA_TRANSFER_BLOCKS_NO_OUTPUT,
}

local_orca_templates_file = os.path.join(
    local_base,
    f"{jobname}_step1_orca_input_templates.txt"
)

paths_text[local_orca_templates_file] = (
    "[minimum]\n"
    + ORCA_INPUT_TEMPLATES["minimum"]
    + "\n[single_point]\n"
    + ORCA_INPUT_TEMPLATES["single_point"]
    + "\n[mecp]\n"
    + ORCA_INPUT_TEMPLATES["mecp"]
)

local_ref_info_file = os.path.join(
    local_base,
    f"{jobname}_Reference_info.txt"
)

ref_lines = [
    "Reference/reactant information",
    "Run mode = CLUSTER",
    f"Reference source = {Reference_source}",
    f"Reference multiplicity = {Reference_multiplicity}",
    f"Charge = {charge}",
    f"MECP input mult_main = {mult_main}",
    f"MECP other mult_other = {mult_other}",
    f"Best interpolation index = {best_record['index']}",
    f"Best interpolation lambda = {best_record['lambda']:.8f}",
    f"Best interpolation abs gap cm-1 = {best_record['gap_abs_cm1']:.8f}",
    f"ORCA input template source = {ORCA_INPUT_TEMPLATE_SOURCE}",
    f"Cluster bash template source = {CLUSTER_SH_TEMPLATE_SOURCE}",
    ""
]

for mult in multiplicities:
    dEh = minima_data[mult]["E_plus_ZPE_Eh"] - E0

    ref_lines += [
        f"[Multiplicity {mult}]",
        f"spin_name = {minima_data[mult]['spin_name']}",
        f"Electronic_Eh = {minima_data[mult]['Electronic_Eh']:.12f}",
        f"ZPE_Eh = {minima_data[mult]['ZPE_Eh']:.12f}",
        f"E_plus_ZPE_Eh = {minima_data[mult]['E_plus_ZPE_Eh']:.12f}",
        f"Relative_cm1 = {dEh * hartree_to_cm:.6f}",
        f"Relative_kcal = {dEh * hartree_to_kcal:.6f}",
        f"remote_out_path = {minima_data[mult]['out_path']}",
        f"remote_xyz_path = {minima_data[mult]['xyz_path']}",
        ""
    ]

paths_text[local_ref_info_file] = "\n".join(ref_lines) + "\n"

for path, text in paths_text.items():
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

remote_uploads = {
    os.path.join(local_mecp, f"{jobname}_MECP_geometry.txt"):
        posixpath.join(remote_mecp, f"{jobname}_MECP_geometry.txt"),

    os.path.join(local_base, f"{jobname}_Reference_geometry.txt"):
        posixpath.join(remote_base, f"{jobname}_Reference_geometry.txt"),

    os.path.join(local_mecp, f"{jobname}_MECP_start_interpolated_geometry.txt"):
        posixpath.join(remote_mecp, f"{jobname}_MECP_start_interpolated_geometry.txt"),

    os.path.join(local_base, f"{jobname}_cluster_sh_template.txt"):
        posixpath.join(remote_base, f"{jobname}_cluster_sh_template.txt"),

    local_orca_templates_file:
        posixpath.join(remote_base, f"{jobname}_step1_orca_input_templates.txt"),

    os.path.join(local_mecp, f"{jobname}_final_MECP_input.inp"):
        posixpath.join(remote_mecp, f"{jobname}_final_MECP_input.inp"),

    local_ref_info_file:
        posixpath.join(remote_base, f"{jobname}_Reference_info.txt"),
}

for local_path, remote_path in remote_uploads.items():
    sftp.put(local_path, remote_path)

# Backward-compatible variables for later steps
reference_source = Reference_source
reference_multiplicity = Reference_multiplicity
use_initial_ref = False
casscf_block = ""

CLUSTER_SH_TEMPLATE_WAS_REVIEWED = True
ORCA_INPUT_TEMPLATES_WERE_REVIEWED = True

# ============================================================
# Final summary
# ============================================================

section("STEP 1 SUMMARY")
print(f"RUN_MODE                      = {RUN_MODE}")
print(f"workflow_mode                 = {workflow_mode}")
print(f"run_mecp_optimization         = {run_mecp_optimization}")
print(f"jobname                       = {jobname}")
print(f"charge                        = {charge}")
print("\nSpin-state summary:")
print(f"  requested multiplicities    = {multiplicities}")
print(f"  mult_main for MECP          = {mult_main}")
print(f"  mult_other for MECP         = {mult_other}")
print(f"  Reference_multiplicity      = {Reference_multiplicity}")
print("\nInterpolation scan:")
print(f"  n_interp                    = {n_interp}")
print(f"  best index                  = {best_record['index']}")
print(f"  best lambda                 = {best_record['lambda']:.8f}")
print(f"  best absolute gap           = {best_record['gap_abs_cm1']:.6f} cm^-1")
print(f"  local scan summary          = {scan_summary_file}")
print(f"  remote scan summary         = {remote_scan_summary_file}")
print(f"  local plot PNG              = {interpolation_plot_png}")
print(f"  local plot PDF              = {interpolation_plot_pdf}")
print(f"  remote plot PNG             = {remote_interpolation_plot_png}")
print(f"  remote plot PDF             = {remote_interpolation_plot_pdf}")
print("\nMethod and resources:")
print(f"  method                      = {method}")
print(f"  basis                       = {basis}")
print(f"  nprocs                      = {nprocs}")
print(f"  maxcore_mb per core         = {maxcore_mb}")
print(f"  approximate total memory    = {nprocs * mem_gb} GB")
print("\nTemplate status:")
print(f"  cluster bash reviewed       = {CLUSTER_SH_TEMPLATE_WAS_REVIEWED}")
print(f"  cluster bash source         = {CLUSTER_SH_TEMPLATE_SOURCE}")
print(f"  submission command reviewed = {CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED}")
print(f"  submission command source   = {CLUSTER_SUBMIT_COMMAND_TEMPLATE_SOURCE}")
print(f"  ORCA inputs reviewed        = {ORCA_INPUT_TEMPLATES_WERE_REVIEWED}")
print(f"  ORCA input source           = {ORCA_INPUT_TEMPLATE_SOURCE}")
print("\nRemote directories:")
print(f"  remote_base                 = {remote_base}")
print(f"  remote_minima               = {remote_minima}")
print(f"  remote_interp               = {remote_interp}")
print(f"  remote_mecp                 = {remote_mecp}")
print("\nLocal directories:")
print(f"  local_base                  = {local_base}")
print(f"  local_minima                = {local_minima}")
print(f"  local_interp                = {local_interp}")
print(f"  local_mecp                  = {local_mecp}")
print("\nPrepared geometries:")
print("  Initial_geometry")
print("  Reference_geometry")
print("  MECP_start_geometry")
print("  MECP_geometry")
print("\nVariables available for later steps:")
print("  RUN_MODE, workflow_mode, run_mecp_optimization")
print("  jobname, charge, multiplicities, mult_main, mult_other")
print("  Reference_multiplicity, reference_multiplicity")
print("  Reference_source, reference_source")
print("  Initial_geometry, Reference_geometry, MECP_start_geometry, MECP_geometry")
print("  method, basis, nprocs, mem_gb, maxcore_mb")
print("  ssh, sftp, cluster_host")
print("  remote_base, remote_minima, remote_interp, remote_mecp")
print("  local_base, local_minima, local_interp, local_mecp")
print("  minima_data, interp_records, best_record")
print("  interpolation_plot_png, interpolation_plot_pdf")
print("  remote_interpolation_plot_png, remote_interpolation_plot_pdf")
print("  low_spin_relative_kcal, high_spin_relative_kcal")
print("  CLUSTER_SH_TEMPLATE, CLUSTER_SH_TEMPLATE_WAS_REVIEWED, CLUSTER_SH_TEMPLATE_SOURCE")
print("  ORCA_INPUT_TEMPLATES, ORCA_INPUT_TEMPLATES_WERE_REVIEWED, ORCA_INPUT_TEMPLATE_SOURCE")
print("  DFT_REVIEWED_PRE_XYZ, ORCA_TRANSFER_BLOCKS_ALL, ORCA_TRANSFER_BLOCKS_NO_OUTPUT")
print("  apply_dft_settings_to_input, make_dft_pre_xyz")
print("  render_cluster_sh, submit_and_monitor_orca_job")
print("\nSTEP 1 COMPLETED SUCCESSFULLY.\n")
print("\nGeometry of the optimized minimum-energy crossing point (MECP):\n")
print(MECP_geometry)


#%% STEP 2. CLUSTER ROHF + ORCA_LOC + AUTOMATIC CASSCF SOC AT MECP

import os
import re
import posixpath
import numpy as np

print(r'''
====================================================================
 STEP 2 | CLUSTER VERSION
 ROHF + UHF/UNO orbital preparation, active-space analysis, and CASSCF SOC
====================================================================

This step uses the MECP geometry from Step 1. A high-spin ROHF
calculation and a high-spin UHF/UNO calculation are performed first.
The ROHF output is parsed for singly occupied molecular orbitals, while
the UHF/UNO output is parsed for natural-orbital occupations. If no
completed SOC .out file exists, the user then chooses whether the
generated SOC input should use ROHF or UHF/UNO active-space information.
If a completed SOC .out file already exists, Step 2 skips that question
and parses the existing SOC output directly. The generated SOC input is
fully editable when a new SOC calculation is needed.

The scalar SOC is evaluated as

  H_SO = sqrt( sum_{Ms,Ms'} |<S,Ms|H_SO|S',Ms'>|^2 )

''')

# ============================================================
# Required variables from Step 1
# ============================================================

required_vars_step2 = [
    "jobname", "MECP_geometry", "charge", "mult_main", "mult_other",
    "multiplicities", "basis", "nprocs", "mem_gb", "maxcore_mb",
    "ssh", "sftp", "remote_base", "local_base",
    "remote_mkdir_p", "remote_file_exists", "remote_read_text",
    "run_ssh", "submit_and_monitor_orca_job"
]

for var in required_vars_step2:
    if var not in globals():
        raise RuntimeError(f"Required variable '{var}' is missing. Run cluster Step 1 first.")

workflow_mode = "MECP_ONLY"

# Step 2 reuses the cluster submission settings selected in Step 1.
# No bash-template or submission-command review is performed in Step 2.

if "CLUSTER_SH_TEMPLATE" not in globals():
    raise RuntimeError("CLUSTER_SH_TEMPLATE is missing. Run Step 1 first.")

if "CLUSTER_SUBMIT_COMMAND_PREFIX" not in globals():
    if "CLUSTER_SUBMIT_COMMAND_TEMPLATE" in globals():
        CLUSTER_SUBMIT_COMMAND_PREFIX = (
            CLUSTER_SUBMIT_COMMAND_TEMPLATE
            .replace("{sh_filename}", "")
            .strip()
        )
    else:
        raise RuntimeError("Cluster submission command is missing. Run Step 1 first.")

CLUSTER_SH_TEMPLATE_WAS_REVIEWED = True
CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = True

# Step 2 must reuse the cluster bash and submission settings from Step 1.
# No bash-template or submission-command review is performed in Step 2.

if "CLUSTER_SH_TEMPLATE" not in globals():
    raise RuntimeError("CLUSTER_SH_TEMPLATE is missing. Run Step 1 first.")

CLUSTER_SH_TEMPLATE_WAS_REVIEWED = True

if "CLUSTER_SUBMIT_COMMAND_PREFIX" in globals():
    CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = True
elif "CLUSTER_SUBMIT_COMMAND_TEMPLATE" in globals():
    CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = True
else:
    raise RuntimeError("Cluster submission command is missing. Run Step 1 first.")

# ============================================================
# User-interface helpers
# ============================================================

def safe_input(prompt=""):
    return input(prompt)


def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def subsection(title):
    print("\n" + "-" * 72)
    print(title)
    print("-" * 72 + "\n")


def ask_yes_no(prompt, default=None):
    if default is True:
        suffix = " (Y/N, default Y): "
    elif default is False:
        suffix = " (Y/N, default N): "
    else:
        suffix = " (Y/N): "

    while True:
        ans = safe_input(prompt + suffix).strip().lower()
        if ans == "" and default is not None:
            return bool(default)
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  Please answer Y or N.")


def ask_float(prompt, default=None, minimum=None):
    while True:
        ans = safe_input(prompt).strip()
        if ans == "" and default is not None:
            return float(default)
        try:
            val = float(ans)
            if minimum is not None and val < minimum:
                print(f"  Input must be at least {minimum}.")
                continue
            return val
        except ValueError:
            print("  Please enter a numerical value.")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_remote_text_file(remote_path):
    with sftp.open(remote_path, "r") as f:
        data = f.read()
    return data.decode(errors="ignore") if isinstance(data, bytes) else str(data)


def remote_text_status(remote_dir, basename):
    remote_out = posixpath.join(remote_dir, f"{basename}.out")
    if not remote_file_exists(sftp, remote_out):
        return "MISSING"
    txt = read_remote_text_file(remote_out)
    if "ORCA TERMINATED NORMALLY" in txt:
        return "OK"
    if "error termination" in txt.lower():
        return "ERROR"
    return "FAILED"


def edit_text_block_if_requested(title, text):
    print(f"\n================ GENERATED {title} ================\n")
    print(text)
    print("====================================================\n")

    if not ask_yes_no(f"Edit {title} before cluster submission?", default=False):
        return text

    print("\nPaste the full modified text below.")
    print("Finish with a line containing only EOF.\n")

    lines = []
    while True:
        line = safe_input()
        if line.strip() == "EOF":
            break
        lines.append(line)

    return "\n".join(lines).rstrip() + "\n"


def spin_S_from_multiplicity(mult):
    return 0.5 * (int(mult) - 1)


def multiplicity_name(mult):
    return {
        1: "singlet", 2: "doublet", 3: "triplet", 4: "quartet", 5: "quintet",
        6: "sextet", 7: "septet", 8: "octet", 9: "nonet", 10: "decet"
    }.get(int(mult), f"mult{mult}")



# ============================================================
# Step 2 ORCA block propagation from Step 1
# ============================================================

def _default_step2_orbital_blocks(include_output=True, uno_output=False):
    text = f"""%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

%scf
  MaxIter 800
  SOSCFStart 1
  LevelShift 0.5
end

"""
    if include_output:
        if uno_output:
            text += """%output
  Print[P_UNO_OccNum] 1
  Print[P_UNO_AtPopMO_M] 1
end

"""
        else:
            text += """%output
  PrintLevel 3
end

"""
    return text

def step2_transfer_blocks(exclude_output=False):
    if exclude_output:
        blocks = globals().get("ORCA_TRANSFER_BLOCKS_NO_OUTPUT", "")
    else:
        blocks = globals().get("ORCA_TRANSFER_BLOCKS_ALL", "")
    if blocks and blocks.strip():
        return blocks.rstrip() + "\n\n"
    return _default_step2_orbital_blocks(include_output=(not exclude_output))

def step2_required_uno_output_block():
    return """%output
  Print[P_UNO_OccNum] 1
  Print[P_UNO_AtPopMO_M] 1
end

"""

def step2_fixed_soc_output_block():
    return """%output
  PrintLevel 3
end

"""

# ============================================================
# Periodic table and geometry helpers
# ============================================================

ATOMIC_NUMBERS = {
    "H": 1, "He": 2,
    "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9, "Ne": 10,
    "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15, "S": 16, "Cl": 17, "Ar": 18,
    "K": 19, "Ca": 20, "Sc": 21, "Ti": 22, "V": 23, "Cr": 24, "Mn": 25, "Fe": 26,
    "Co": 27, "Ni": 28, "Cu": 29, "Zn": 30, "Ga": 31, "Ge": 32, "As": 33, "Se": 34,
    "Br": 35, "Kr": 36, "Rb": 37, "Sr": 38, "Y": 39, "Zr": 40, "Nb": 41, "Mo": 42,
    "Tc": 43, "Ru": 44, "Rh": 45, "Pd": 46, "Ag": 47, "Cd": 48, "In": 49, "Sn": 50,
    "Sb": 51, "Te": 52, "I": 53, "Xe": 54, "Cs": 55, "Ba": 56, "La": 57, "Ce": 58,
    "Pr": 59, "Nd": 60, "Pm": 61, "Sm": 62, "Eu": 63, "Gd": 64, "Tb": 65, "Dy": 66,
    "Ho": 67, "Er": 68, "Tm": 69, "Yb": 70, "Lu": 71, "Hf": 72, "Ta": 73, "W": 74,
    "Re": 75, "Os": 76, "Ir": 77, "Pt": 78, "Au": 79, "Hg": 80, "Tl": 81, "Pb": 82,
    "Bi": 83, "Po": 84, "At": 85, "Rn": 86
}

TRANSITION_METALS = {
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg"
}


def parse_geometry_symbols(geom_text):
    symbols = []
    for line in geom_text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            sym = re.sub(r"[^A-Za-z]", "", parts[0])
            sym = sym[0].upper() + sym[1:].lower()
            symbols.append(sym)
    if not symbols:
        raise RuntimeError("Could not parse atom symbols from MECP_geometry.")
    return symbols


def infer_metal_symbol_from_geometry(geom_text):
    metals = [sym for sym in parse_geometry_symbols(geom_text) if sym in TRANSITION_METALS]
    if not metals:
        raise RuntimeError("No transition-metal atom was detected in MECP_geometry.")
    if len(set(metals)) > 1:
        print("Multiple transition-metal symbols were detected:")
        print(f"  {sorted(set(metals))}")
        print("The first transition-metal symbol in the geometry will be used for SOMO localization analysis.")
    return metals[0]


def total_electrons_from_geometry_and_charge(geom_text, molecular_charge):
    z_sum = 0
    missing = []
    for sym in parse_geometry_symbols(geom_text):
        if sym not in ATOMIC_NUMBERS:
            missing.append(sym)
        else:
            z_sum += ATOMIC_NUMBERS[sym]
    if missing:
        raise RuntimeError(f"Missing atomic numbers for: {sorted(set(missing))}")
    return int(z_sum - molecular_charge)

# ============================================================
# ROHF SOMO parsing
# ============================================================

def parse_singly_occupied_orbitals_from_rohf_out(out_text, occ_target=1.0, tol=1.0e-4):
    marker = "ORBITAL ENERGIES"
    if marker not in out_text:
        raise RuntimeError("Could not find the ORBITAL ENERGIES section in the ROHF output.")

    section_text = out_text.split(marker, 1)[1]
    somo_indices = []
    orbital_rows = []

    for raw in section_text.splitlines():
        parts = raw.split()
        if len(parts) >= 4:
            try:
                mo_index = int(parts[0])
                occ = float(parts[1])
                energy_eh = float(parts[2])
                energy_ev = float(parts[3])
                orbital_rows.append((mo_index, occ, energy_eh, energy_ev))
                if abs(occ - occ_target) <= tol:
                    somo_indices.append(mo_index)
            except ValueError:
                if orbital_rows:
                    break
        elif orbital_rows:
            break

    if not orbital_rows:
        raise RuntimeError("The ORBITAL ENERGIES section was found, but no orbital rows were parsed.")
    if not somo_indices:
        raise RuntimeError("No singly occupied orbitals with OCC approximately equal to 1.0000 were found.")
    return somo_indices, orbital_rows

def parse_uno_occupations_from_uhf_out(out_text):
    """Parse ORCA's UHF NATURAL ORBITALS occupation-number list.

    Returns a sorted list of (orbital_index, occupation). ORCA prints natural
    orbital indices as N[  i] with occupations between 0 and 2.
    """
    marker = "UHF NATURAL ORBITALS"
    if marker not in out_text:
        raise RuntimeError("Could not find the UHF NATURAL ORBITALS section in the UHF/UNO output.")

    section_text = out_text.split(marker, 1)[1]
    matches = re.findall(
        r"N\[\s*(\d+)\s*\]\s*=\s*([-+]?\d*\.\d+(?:[Ee][-+]?\d+)?)",
        section_text
    )

    if not matches:
        raise RuntimeError("UHF NATURAL ORBITALS was found, but no N[i] occupation values were parsed.")

    occupations = [(int(i), float(occ)) for i, occ in matches]
    occupations.sort(key=lambda x: x[0])
    return occupations


def select_uno_active_orbitals(uno_occupations, low=0.02, high=1.98, somo_tol=1.0e-4):
    active = [(idx, occ) for idx, occ in uno_occupations if occ > low and occ < high]
    somos = [(idx, occ) for idx, occ in uno_occupations if abs(occ - 1.0) <= somo_tol]

    active_indices = [idx for idx, occ in active]
    active_electrons = int(round(sum(occ for idx, occ in active)))
    active_orbitals = len(active_indices)

    return {
        "active": active,
        "somos": somos,
        "active_indices": active_indices,
        "active_electrons": active_electrons,
        "active_orbitals": active_orbitals,
        "low": low,
        "high": high,
        "somo_tol": somo_tol,
    }

# ============================================================
# orca_loc helpers
# ============================================================

def detect_orca_loc_command_from_cluster_template():
    template_text = globals().get("CLUSTER_SH_TEMPLATE", "")
    for line in template_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.search(r"(\S*/orca)\s+\S+\.inp", stripped)
        if match:
            return match.group(1).rsplit("/", 1)[0] + "/orca_loc"
    return "/usr/local/apps/orca/5.0.4/orca_loc"


def build_locinp_text(gbw_filename, loc_filename, first_somo, last_somo):
    return f"""{gbw_filename}
{loc_filename}
{int(first_somo)}
{int(last_somo)}
1
"""


def run_orca_loc(remote_dir, locinp_filename):
    orca_loc_command = detect_orca_loc_command_from_cluster_template()
    loc_stdout_filename = locinp_filename.rsplit(".", 1)[0] + ".orca_loc.out"
    cmd = (
        f'cd "{remote_dir}" && '
        f'{orca_loc_command} {locinp_filename} > {loc_stdout_filename} 2>&1'
    )

    section("RUNNING ORCA_LOC")
    print(f"Remote directory : {remote_dir}")
    print(f"orca_loc command : {orca_loc_command}")
    print(f"Input file       : {locinp_filename}")
    print(f"Output capture   : {loc_stdout_filename}")

    _, err = run_ssh(ssh, cmd)
    if err.strip():
        print("Remote shell message:")
        print(err)

    remote_stdout_path = posixpath.join(remote_dir, loc_stdout_filename)
    if not remote_file_exists(sftp, remote_stdout_path):
        raise RuntimeError(f"orca_loc output capture was not created:\n{remote_stdout_path}")
    return read_remote_text_file(remote_stdout_path), remote_stdout_path, orca_loc_command


def parse_lmo_compositions_from_orca_loc_output(loc_output_text, somo_indices):
    somo_set = {int(x) for x in somo_indices}
    records = {}
    line_pattern = re.compile(r"MO\s+(\d+):(.+)")
    atom_pattern = re.compile(r"(\d+)([A-Za-z]+)\s*-\s*([+-]?\d+\.\d+)")

    for raw in loc_output_text.splitlines():
        m = line_pattern.search(raw)
        if not m:
            continue
        mo_index = int(m.group(1))
        if mo_index not in somo_set:
            continue
        contributions = []
        for am in atom_pattern.finditer(m.group(2)):
            contributions.append({
                "atom_index": int(am.group(1)),
                "atom_symbol": am.group(2),
                "population": float(am.group(3))
            })
        if contributions:
            records[mo_index] = contributions

    missing = sorted(somo_set - set(records))
    if missing:
        raise RuntimeError(
            "The orca_loc output did not contain localized-orbital composition records "
            f"for SOMOs: {missing}"
        )
    return records


def choose_soc_protocol_from_lmo_records(lmo_records, metal_symbol, metal_population_threshold=0.93):
    diagnostics = []
    all_somos_metal_centered = True

    for mo_index in sorted(lmo_records):
        contributions = lmo_records[mo_index]
        metal_population = sum(
            item["population"] for item in contributions
            if item["atom_symbol"].lower() == metal_symbol.lower()
        )
        dominant = max(contributions, key=lambda x: x["population"])
        metal_is_dominant = dominant["atom_symbol"].lower() == metal_symbol.lower()
        passes_threshold = metal_population >= metal_population_threshold

        diagnostics.append({
            "mo_index": mo_index,
            "metal_population": metal_population,
            "dominant_atom_index": dominant["atom_index"],
            "dominant_atom_symbol": dominant["atom_symbol"],
            "dominant_population": dominant["population"],
            "metal_is_dominant": metal_is_dominant,
            "passes_threshold": passes_threshold
        })

        if (not metal_is_dominant) or (not passes_threshold):
            all_somos_metal_centered = False

    if all_somos_metal_centered:
        return (
            "CASSCF_MAXITER_1",
            "All localized SOMOs are metal-dominated and pass the metal-population threshold.",
            diagnostics
        )

    return (
        "CASSCF_MAXITER_1",
        "At least one localized SOMO has substantial ligand character or metal population below threshold.",
        diagnostics
    )

# ============================================================
# SOC parsing and definitions
# ============================================================

def parse_orca_soc_matrix_elements(soc_output_text):
    marker = "NONZERO SOC MATRIX ELEMENTS (cm**-1)"
    if marker not in soc_output_text:
        raise RuntimeError("Could not find NONZERO SOC MATRIX ELEMENTS (cm**-1) in the SOC output.")

    soc_section = soc_output_text.split(marker, 1)[1]
    values = []

    for raw in soc_section.splitlines():
        stripped = raw.strip()
        if "Note:" in stripped:
            break
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 10:
            continue
        try:
            bra_block = int(parts[0])
            bra_root = int(parts[1])
            bra_S = float(parts[2])
            bra_Ms = float(parts[3])
            ket_block = int(parts[4])
            ket_root = int(parts[5])
            ket_S = float(parts[6])
            ket_Ms = float(parts[7])
            real_part = float(parts[-2])
            imag_part = float(parts[-1])
        except ValueError:
            continue

        values.append({
            "bra_block": bra_block,
            "bra_root": bra_root,
            "bra_S": bra_S,
            "bra_Ms": bra_Ms,
            "ket_block": ket_block,
            "ket_root": ket_root,
            "ket_S": ket_S,
            "ket_Ms": ket_Ms,
            "real": real_part,
            "imag": imag_part,
            "complex": complex(real_part, imag_part),
            "abs_cm1": float(np.sqrt(real_part**2 + imag_part**2)),
            "sq_cm2": float(real_part**2 + imag_part**2),
            "intermultiplicity": abs(bra_S - ket_S) > 1.0e-8,
            "line": stripped
        })
    return values


def build_ms_resolved_soc_matrix(soc_values, mult_a, mult_b):
    S_a = spin_S_from_multiplicity(mult_a)
    S_b = spin_S_from_multiplicity(mult_b)
    S_low = min(S_a, S_b)
    S_high = max(S_a, S_b)
    Ms_low = np.arange(S_low, -S_low - 1, -1, dtype=float)
    Ms_high = np.arange(S_high, -S_high - 1, -1, dtype=float)
    matrix = np.zeros((len(Ms_low), len(Ms_high)), dtype=complex)

    for item in [x for x in soc_values if x["intermultiplicity"]]:
        value = item["complex"]
        if abs(item["bra_S"] - S_low) < 1.0e-8 and abs(item["ket_S"] - S_high) < 1.0e-8:
            i = int(np.where(np.isclose(Ms_low, item["bra_Ms"]))[0][0])
            j = int(np.where(np.isclose(Ms_high, item["ket_Ms"]))[0][0])
            matrix[i, j] = value
        elif abs(item["bra_S"] - S_high) < 1.0e-8 and abs(item["ket_S"] - S_low) < 1.0e-8:
            i = int(np.where(np.isclose(Ms_low, item["ket_Ms"]))[0][0])
            j = int(np.where(np.isclose(Ms_high, item["bra_Ms"]))[0][0])
            matrix[i, j] = np.conjugate(value)
    return S_low, S_high, Ms_low, Ms_high, matrix


def effective_soc_eq_221(soc_matrix):
    return float(np.sqrt(np.sum(np.abs(soc_matrix)**2)))


def channel_soc_dictionary(S_low, S_high, Ms_low, Ms_high, soc_matrix):
    channels = {}
    for i, Ms_i in enumerate(Ms_low):
        for j, Ms_j in enumerate(Ms_high):
            val = soc_matrix[i, j]
            if abs(val) > 1.0e-12:
                key = f"S{S_low:.1f}_Ms{Ms_i:+.1f}_to_S{S_high:.1f}_Ms{Ms_j:+.1f}"
                channels[key] = val
    return channels

# ============================================================
# Directories and filenames
# ============================================================

remote_soc = posixpath.join(remote_base, "SOC")
remote_mkdir_p(sftp, remote_soc)
local_soc = os.path.join(local_base, "SOC")
os.makedirs(local_soc, exist_ok=True)

orb_jobname = f"{jobname}_ROHF_HS"
orb_inp_filename = f"{orb_jobname}.inp"
orb_out_filename = f"{orb_jobname}.out"
orb_gbw_filename = f"{orb_jobname}.gbw"

uhf_jobname = f"{jobname}_UHF_UNO_HS"
uhf_inp_filename = f"{uhf_jobname}.inp"
uhf_out_filename = f"{uhf_jobname}.out"
uhf_gbw_filename = f"{uhf_jobname}.gbw"
uhf_uno_filename = f"{uhf_jobname}.uno"

locinp_filename = f"{orb_jobname}.locinp"
loc_filename = f"{orb_jobname}.loc"

soc_jobname = f"{jobname}_SOC"
soc_inp_filename = f"{soc_jobname}.inp"
soc_out_filename = f"{soc_jobname}.out"

remote_orb_out = posixpath.join(remote_soc, orb_out_filename)
remote_orb_gbw = posixpath.join(remote_soc, orb_gbw_filename)
remote_uhf_out = posixpath.join(remote_soc, uhf_out_filename)
remote_uhf_gbw = posixpath.join(remote_soc, uhf_gbw_filename)
remote_uhf_uno = posixpath.join(remote_soc, uhf_uno_filename)
remote_locinp = posixpath.join(remote_soc, locinp_filename)
remote_loc_file = posixpath.join(remote_soc, loc_filename)
remote_soc_out = posixpath.join(remote_soc, soc_out_filename)
remote_soc_inp = posixpath.join(remote_soc, soc_inp_filename)

section("STEP 2 DIRECTORY PREPARATION")
print(f"Remote SOC directory : {remote_soc}")
print(f"Local SOC directory  : {local_soc}")

# Check this early so an already-completed SOC calculation is parsed directly.
# In that case, Step 2 must not ask whether to generate the SOC input from
# ROHF or UHF/UNO orbitals, because no new SOC input is needed.
soc_status_preexisting = remote_text_status(remote_soc, soc_jobname)
soc_output_already_completed = (soc_status_preexisting == "OK")

if soc_output_already_completed:
    print(f"Existing completed SOC output found for {soc_jobname}.")
    print("Step 2 will parse the existing SOC output directly.")
    print("No ROHF/UHF orbital-source question will be asked for SOC generation.")
else:
    print(f"No completed SOC output found for {soc_jobname}.")
    print("A new SOC input will be generated after orbital diagnostics.")

# ============================================================
# High-spin ROHF orbital preparation
# ============================================================

section("ROHF HIGH-SPIN ORBITAL PREPARATION")

soc_orbital_mult = max(multiplicities)
orbital_method = "ROHF"

print(f"High-spin ROHF multiplicity : {soc_orbital_mult}")
print("ROHF output will be parsed automatically for OCC = 1.0000 orbitals.")


def build_rohf_hs_input():
    blocks = step2_transfer_blocks(exclude_output=False)
    return f"""! {orbital_method} {basis} TightSCF SlowConv

{blocks}* xyz {charge} {soc_orbital_mult}
{MECP_geometry}
*
"""

orb_status = remote_text_status(remote_soc, orb_jobname)
gbw_exists = remote_file_exists(sftp, remote_orb_gbw)

orbital_inp_text = build_rohf_hs_input()

if orb_status == "OK" and gbw_exists:
    print(f"Completed ROHF output and GBW already exist for {orb_jobname}.")
    print("Skipping ROHF submission. Existing ROHF output and GBW will be used.")

else:
    status, orbital_inp_text = submit_and_monitor_orca_job(
        remote_dir=remote_soc,
        jobname_i=orb_jobname,
        inp_text=orbital_inp_text,
        job_label="ROHF high-spin orbital preparation for SOC",
        allow_interactive_repair=True,
        repair_policy="rohf_restricted",
        locked={
            "method": orbital_method,
            "basis": basis,
            "charge": charge,
            "mult": soc_orbital_mult,
            "geom": MECP_geometry,
        }
    )

    if status != "OK":
        raise SystemExit(
            "ROHF high-spin orbital preparation did not complete successfully."
        )

if not remote_file_exists(sftp, remote_orb_out):
    raise RuntimeError(
        f"ROHF output file was not found:\n{remote_orb_out}"
    )

if not remote_file_exists(sftp, remote_orb_gbw):
    raise RuntimeError(
        f"ROHF GBW file was not found:\n{remote_orb_gbw}"
    )

# ============================================================
# SOMO detection
# ============================================================

section("ROHF SOMO DETECTION")

rohf_out_text = read_remote_text_file(remote_orb_out)
rohf_somo_indices, rohf_orbital_rows = parse_singly_occupied_orbitals_from_rohf_out(rohf_out_text)

if sorted(rohf_somo_indices) != list(range(min(rohf_somo_indices), max(rohf_somo_indices) + 1)):
    raise RuntimeError(
        "Detected SOMOs are not contiguous; automatic orca_loc localization "
        "requires a contiguous SOMO window."
    )

first_somo = min(rohf_somo_indices)
last_somo = max(rohf_somo_indices)
nel_soc = len(rohf_somo_indices)
norb_soc = len(rohf_somo_indices)
total_electrons = total_electrons_from_geometry_and_charge(MECP_geometry, charge)

print(f"Total electrons from geometry and charge : {total_electrons}")
print(f"Detected ROHF SOMOs                      : {rohf_somo_indices}")
print(f"Standard SOC active space                : CAS({nel_soc},{norb_soc})")
print(f"First SOMO                               : {first_somo}")
print(f"Last SOMO                                : {last_somo}")

print("\nDetected SOMO rows from the ORBITAL ENERGIES section:")
for mo, occ, eeh, eev in rohf_orbital_rows:
    if mo in rohf_somo_indices:
        print(f"  MO {mo:4d}   OCC={occ:.4f}   E={eeh: .8f} Eh   {eev: .4f} eV")

# ============================================================
# UHF/UNO orbital preparation and active-space detection
# ============================================================

section("UHF/UNO ORBITAL PREPARATION")

print(f"High-spin UHF/UNO multiplicity : {soc_orbital_mult}")
print("UHF/UNO output will be parsed for natural-orbital occupations.")


def build_uhf_uno_hs_input():
    blocks = step2_transfer_blocks(exclude_output=True)
    output_block = step2_required_uno_output_block()
    return f"""! UHF {basis} TightSCF SlowConv UNO

{blocks}{output_block}* xyz {charge} {soc_orbital_mult}
{MECP_geometry}
*
"""

uhf_status = remote_text_status(remote_soc, uhf_jobname)
uhf_out_exists = remote_file_exists(sftp, remote_uhf_out)
uhf_input_text = build_uhf_uno_hs_input()

if uhf_status == "OK" and uhf_out_exists:
    print(f"Completed UHF/UNO output already exists for {uhf_jobname}.")
    print("Skipping UHF/UNO submission. Existing UHF/UNO output will be used.")

else:
    status, uhf_input_text = submit_and_monitor_orca_job(
        remote_dir=remote_soc,
        jobname_i=uhf_jobname,
        inp_text=uhf_input_text,
        job_label="UHF/UNO high-spin natural-orbital preparation for SOC",
        allow_interactive_repair=True,
        repair_policy="rohf_restricted",
        locked={
            "method": "UHF",
            "basis": basis,
            "charge": charge,
            "mult": soc_orbital_mult,
            "geom": MECP_geometry,
        }
    )

    if status != "OK":
        raise SystemExit("UHF/UNO high-spin orbital preparation did not complete successfully.")

if not remote_file_exists(sftp, remote_uhf_out):
    raise RuntimeError(f"UHF/UNO output file was not found:\n{remote_uhf_out}")

uhf_out_text = read_remote_text_file(remote_uhf_out)
uno_occupations = parse_uno_occupations_from_uhf_out(uhf_out_text)
uno_selection = select_uno_active_orbitals(
    uno_occupations,
    low=0.02,
    high=1.98,
    somo_tol=1.0e-4
)

uhf_uno_somo_indices = [idx for idx, occ in uno_selection["somos"]]
uhf_uno_active_indices = uno_selection["active_indices"]
uhf_uno_active_electrons = uno_selection["active_electrons"]
uhf_uno_active_orbitals = uno_selection["active_orbitals"]

if uhf_uno_active_orbitals <= 0:
    raise RuntimeError("No UHF/UNO active orbitals were found with 0.02 < occupation < 1.98.")

print("\nUHF natural-orbital SOMO-like occupations, OCC ≈ 1.0000:")
if uno_selection["somos"]:
    for idx, occ in uno_selection["somos"]:
        print(f"  UNO {idx:4d}   occupation = {occ:.6f}")
else:
    print("  No exactly SOMO-like UNO occupations were detected.")

print("\nUHF natural orbitals selected by 0.02 < occupation < 1.98:")
for idx, occ in uno_selection["active"]:
    print(f"  UNO {idx:4d}   occupation = {occ:.6f}")

print(f"\nUHF/UNO active space from occupation window : CAS({uhf_uno_active_electrons},{uhf_uno_active_orbitals})")
print(f"UHF/UNO active orbital indices             : {uhf_uno_active_indices}")

# ============================================================
# orca_loc diagnostic
# ============================================================

section("LOCALIZED SOMO DIAGNOSTIC WITH ORCA_LOC")

locinp_text = build_locinp_text(orb_gbw_filename, loc_filename, first_somo, last_somo)
print("Generated orca_loc input:")
print(locinp_text)

local_locinp = os.path.join(local_soc, locinp_filename)
write_local_text(local_locinp, locinp_text)
sftp.put(local_locinp, remote_locinp)

try:
    orca_loc_output_text, remote_orca_loc_stdout, orca_loc_command = run_orca_loc(
        remote_soc,
        locinp_filename
    )

    local_orca_loc_stdout = os.path.join(
        local_soc,
        os.path.basename(remote_orca_loc_stdout)
    )
    write_local_text(local_orca_loc_stdout, orca_loc_output_text)

    try:
        sftp.get(remote_loc_file, os.path.join(local_soc, loc_filename))
    except Exception:
        pass

    print("\nORCA localized-orbital composition output:")
    print(orca_loc_output_text)

except Exception as err:
    print("WARNING: orca_loc localization analysis failed.")
    print("Step 2 will continue because ROHF SOMOs were already detected from the ROHF output.")
    print(f"orca_loc error: {err}")
    orca_loc_output_text = ""
    remote_orca_loc_stdout = ""
    orca_loc_command = "orca_loc unavailable or failed"

# ============================================================
# SOC orbital-source selection
# ============================================================

section("SOC ORBITAL SOURCE SELECTION")

metal_symbol = infer_metal_symbol_from_geometry(MECP_geometry)
metal_population_threshold = 0.93

lmo_records = {}
lmo_diagnostics = []

if orca_loc_output_text.strip():
    try:
        lmo_records = parse_lmo_compositions_from_orca_loc_output(
            orca_loc_output_text,
            rohf_somo_indices
        )

        _, _, lmo_diagnostics = choose_soc_protocol_from_lmo_records(
            lmo_records,
            metal_symbol,
            metal_population_threshold
        )
    except Exception as err:
        print("WARNING: Could not parse localized SOMO diagnostics from orca_loc output.")
        print(f"Diagnostic parsing error: {err}")
        lmo_records = {}
        lmo_diagnostics = []

print(f"Detected metal symbol      : {metal_symbol}")
print(f"Diagnostic metal threshold : {metal_population_threshold:.2f}")

print("\nROHF active-space option:")
print(f"  ROHF SOMOs              : {rohf_somo_indices}")
print(f"  ROHF active space       : CAS({nel_soc},{norb_soc})")

print("\nUHF/UNO active-space option:")
print(f"  UNO SOMO-like orbitals  : {uhf_uno_somo_indices}")
print(f"  UNO active orbitals     : {uhf_uno_active_indices}")
print(f"  UNO active space        : CAS({uhf_uno_active_electrons},{uhf_uno_active_orbitals})")

print("\nLocalized SOMO diagnostics:")
if lmo_diagnostics:
    for item in lmo_diagnostics:
        print(
            f"  MO {item['mo_index']:4d} | "
            f"{metal_symbol} population = {item['metal_population']:.6f} | "
            f"dominant atom = {item['dominant_atom_index']}{item['dominant_atom_symbol']} "
            f"({item['dominant_population']:.6f}) | "
            f"passes diagnostic threshold = {item['passes_threshold']}"
        )
else:
    print("  No localized-orbital diagnostics available.")
    
if soc_output_already_completed:
    print("\nExisting SOC output was found before SOC input generation.")
    print("Therefore, the ROHF/UHF orbital-source prompt is skipped.")
    print("The active-space values below are retained only as diagnostics from the ROHF parser.")

    soc_orbital_source = "EXISTING_SOC_OUTPUT"
    selected_soc_keyword = None
    selected_soc_header_extra = ""
    selected_active_indices = rohf_somo_indices
    nel_soc_selected = nel_soc
    norb_soc_selected = norb_soc

else:
    print("\nReference orbital options for the SOC calculation")
    print("-------------------------------------------------")
    print("ROHF (recommended, default)")
    print("  • Uses restricted open-shell Hartree–Fock orbitals.")
    print("  • Recommended for most transition-metal systems.")
    print()
    print("UHF/UNO (advanced)")
    print("  • Uses unrestricted Hartree–Fock orbitals together with")
    print("    unrestricted natural orbitals (UNO) for active-space selection.")
    print("  • Intended primarily for difficult open-shell systems where")
    print("    ROHF convergence or orbital quality may be problematic.")
    print()
    print("In many applications, ROHF and UHF/UNO lead to similar active")
    print("spaces and effective SOC values. UHF/UNO is provided primarily")
    print("for challenging open-shell systems where ROHF convergence or")
    print("orbital quality may be problematic.")

    while True:
        orbital_choice = safe_input("\nUse which orbitals for the generated SOC input? Enter ROHF or UHF (default ROHF): ").strip().lower()
        if orbital_choice == "":
            orbital_choice = "rohf"
        if orbital_choice in ("rohf", "r"):
            soc_orbital_source = "ROHF"
            selected_soc_keyword = "ROHF"
            selected_soc_header_extra = ""
            selected_active_indices = rohf_somo_indices
            nel_soc_selected = nel_soc
            norb_soc_selected = norb_soc
            break
        if orbital_choice in ("uhf", "uno", "u"):
            soc_orbital_source = "UHF_UNO"
            selected_soc_keyword = "UHF"
            selected_soc_header_extra = "UNO"
            selected_active_indices = uhf_uno_active_indices
            nel_soc_selected = uhf_uno_active_electrons
            norb_soc_selected = uhf_uno_active_orbitals
            break
        print("  Please enter ROHF or UHF.")

nel_soc = int(nel_soc_selected)
norb_soc = int(norb_soc_selected)
soc_active_indices = list(selected_active_indices)

if soc_output_already_completed:
    soc_protocol = "EXISTING_SOC_OUTPUT_PARSED_DIRECTLY"
    soc_protocol_reason = (
        "A completed SOC .out file already existed before SOC input generation. "
        "Step 2 skipped the ROHF/UHF orbital-source prompt and parsed the existing "
        "SOC output directly. The listed active orbitals are diagnostic only and "
        "were not used to regenerate or resubmit the SOC calculation."
    )
else:
    soc_protocol = f"CASSCF_MAXITER_1_SOC_{soc_orbital_source}"
    soc_protocol_reason = (
        f"The generated SOC input uses an inline {soc_orbital_source} reference "
        f"followed by CAS({nel_soc},{norb_soc}) inside a CASSCF block with "
        "maxiter 1 and dosoc true. No %moinp/MoRead is used. "
        "ROHF is the recommended default, while UHF/UNO is available as an "
        "advanced option for challenging open-shell systems."
    )

print(f"\nSelected SOC orbital source          : {soc_orbital_source}")
print(f"Selected active orbitals             : {soc_active_indices}")
print(f"Selected active space                : CAS({nel_soc},{norb_soc})")
print(f"Selected SOC protocol label          : {soc_protocol}")
print(f"Selection rationale                  : {soc_protocol_reason}")

# ============================================================
# SOC input construction
# ============================================================

section("SOC INPUT CONSTRUCTION")

soc_mults = [int(mult_main), int(mult_other)]


rel_block = """%rel
  SOCType 3
  SOCFlags 1,4,3,0
  SOCMaxCenter 4
end

"""


def build_casscf_soc_input():
    mult_list = ",".join(str(int(m)) for m in soc_mults)
    nroots_list = ",".join("1" for _ in soc_mults)

    if soc_orbital_source == "ROHF":
        header = f"! ROHF {basis} TightSCF SlowConv"
    elif soc_orbital_source == "UHF_UNO":
        header = f"! UHF {basis} TightSCF SlowConv UNO"
    else:
        raise RuntimeError(f"Cannot build a new SOC input for orbital source: {soc_orbital_source}")

    blocks = step2_transfer_blocks(exclude_output=True)
    output_block = step2_fixed_soc_output_block()

    return f"""{header}

{blocks}{rel_block}%casscf
  maxiter 1
  mult {mult_list}
  nroots {nroots_list}
  nel {nel_soc}
  nOrb {norb_soc}

  rel
  dosoc true
  dossc false
  PrintLevel 3
  TPrint 0.01
  end
end

{output_block}* xyz {charge} {max(soc_mults)}
{MECP_geometry}
*
"""


soc_status = soc_status_preexisting

if soc_output_already_completed:
    print(f"Existing completed SOC output found for {soc_jobname}.")
    print("Skipping SOC input construction, ROHF/UHF selection, editing, and submission.")
    print("Existing SOC output will be used.")
    rerun_soc = False
    soc_job_label = "Existing CASSCF SOC calculation"

    if remote_file_exists(sftp, remote_soc_inp):
        try:
            soc_inp_text_default = read_remote_text_file(remote_soc_inp)
        except Exception:
            soc_inp_text_default = "Existing SOC input could not be read."
    else:
        soc_inp_text_default = "Existing SOC input was not found; existing SOC output is parsed directly."

else:
    soc_inp_text_default = build_casscf_soc_input()
    soc_job_label = f"Editable CASSCF SOC calculation ({soc_orbital_source} orbitals)"

    print(f"Protocol selected for SOC input : {soc_protocol}")
    print(f"Orbital source in generated input: {soc_orbital_source}")
    print(f"Active space used in SOC input  : CAS({nel_soc},{norb_soc})")
    print("\nThe generated SOC input is fully editable before running.")
    print("You may replace or modify the %casscf block before submission if needed.")
    print("Because this input is intentionally editable, failure-repair validation uses free_full.")

    print(f"No completed SOC output found for {soc_jobname}.")
    print("Submitting SOC calculation.")
    rerun_soc = True

if rerun_soc:
    soc_inp_text = edit_text_block_if_requested(
        soc_inp_filename,
        soc_inp_text_default
    )

    status, soc_inp_text = submit_and_monitor_orca_job(
        remote_dir=remote_soc,
        jobname_i=soc_jobname,
        inp_text=soc_inp_text,
        job_label=soc_job_label,
        allow_interactive_repair=True,
        repair_policy="free_full",
        locked={}
    )

    if status != "OK":
        raise SystemExit("SOC calculation did not complete successfully.")

else:
    soc_inp_text = soc_inp_text_default

if not remote_file_exists(sftp, remote_soc_out):
    raise RuntimeError(f"SOC output file was not found:\n{remote_soc_out}")
    
def extract_general_intermediate_soc_components(S_low, S_high, Ms_low, Ms_high, soc_matrix):
    components = {}
    z_count = 1
    ib_count = 1
    other_count = 1

    for i, Ms_i in enumerate(Ms_low):
        for j, Ms_j in enumerate(Ms_high):
            value = soc_matrix[i, j]

            if abs(value) <= 1.0e-12:
                continue

            delta_ms = Ms_j - Ms_i

            if abs(delta_ms) < 1.0e-8:
                key = f"z{z_count}"
                z_count += 1

            elif abs(abs(delta_ms) - 1.0) < 1.0e-8:
                key = f"ib{ib_count}"
                ib_count += 1

            else:
                key = f"other{other_count}_dMs_{delta_ms:+.1f}"
                other_count += 1

            components[key] = value

    return components


def effective_soc_from_intermediate_components(components):
    active_components = {
        key: value
        for key, value in components.items()
        if key.startswith("z") or key.startswith("ib")
    }

    return float(
        np.sqrt(
            np.sum([abs(value)**2 for value in active_components.values()])
        )
    )


# ============================================================
# Parse SOC output
# ============================================================

section("SOC MATRIX ELEMENT EXTRACTION")

soc_out_text = read_remote_text_file(remote_soc_out)
soc_values = parse_orca_soc_matrix_elements(soc_out_text)

if not soc_values:
    print("The SOC table is present, but no nonzero matrix elements were printed.")
    print("The effective SOC is therefore assigned as zero.")

S_low, S_high, Ms_low, Ms_high, SOC_Ms_matrix_cm1 = build_ms_resolved_soc_matrix(
    soc_values,
    soc_mults[0],
    soc_mults[1]
)

SOC_Ms_abs_cm1 = np.abs(SOC_Ms_matrix_cm1)

H_SO_channels_cm1 = channel_soc_dictionary(
    S_low,
    S_high,
    Ms_low,
    Ms_high,
    SOC_Ms_matrix_cm1
)

H_SO_intermediate_components_cm1 = extract_general_intermediate_soc_components(
    S_low,
    S_high,
    Ms_low,
    Ms_high,
    SOC_Ms_matrix_cm1
)

H_SO_all_matrix_elements_cm = effective_soc_eq_221(SOC_Ms_matrix_cm1)

# Eq. 2.21 is the full Ms-resolved matrix norm, not an RMS average
# and not a unique-component norm.
H_SO_ORCA_effective_cm = H_SO_all_matrix_elements_cm

H_SO_ST_components_cm1 = {}
H_SO_DQ_components_cm1 = {}
H_SO_TQ_components_cm1 = {}
H_SO_QS_components_cm1 = {}

if abs(S_low - 0.0) < 1.0e-8 and abs(S_high - 1.0) < 1.0e-8:
    H_SO_ST_components_cm1 = H_SO_intermediate_components_cm1

elif abs(S_low - 0.5) < 1.0e-8 and abs(S_high - 1.5) < 1.0e-8:
    H_SO_DQ_components_cm1 = H_SO_intermediate_components_cm1

elif abs(S_low - 1.0) < 1.0e-8 and abs(S_high - 2.0) < 1.0e-8:
    H_SO_TQ_components_cm1 = H_SO_intermediate_components_cm1

elif abs(S_low - 1.5) < 1.0e-8 and abs(S_high - 2.5) < 1.0e-8:
    H_SO_QS_components_cm1 = H_SO_intermediate_components_cm1

SOC_CONVENTION_FACTOR = 1.0
SOC_CONVENTION_LABEL = (
    "Eq. 2.21 full Ms-resolved SOC matrix norm; "
    "sqrt(sum over all printed intermultiplicity Ms/Ms_prime channels |H|^2)"
)

H_SO_cm = H_SO_ORCA_effective_cm
H_SO_source = "ORCA SOC calculation"

print(f"Low-spin S value       : {S_low:.1f}")
print(f"High-spin S value      : {S_high:.1f}")
print(f"Low-spin Ms values     : {Ms_low}")
print(f"High-spin Ms values    : {Ms_high}")

print("\nMs-resolved complex SOC matrix in cm^-1:")
print(SOC_Ms_matrix_cm1)

print("\nAbsolute Ms-resolved SOC matrix in cm^-1:")
print(SOC_Ms_abs_cm1)

print("\nAll nonzero Ms-resolved SOC channels:")
if H_SO_channels_cm1:
    for key, value in H_SO_channels_cm1.items():
        print(
            f"  {key:35s} = "
            f"{value.real: .6f} {value.imag:+.6f}i cm^-1   "
            f"|H| = {abs(value):.6f}"
        )
else:
    print("  No nonzero SOC channels were printed by ORCA.")

print("\nIntermediate SOC components used in Eq. 6:")
if H_SO_intermediate_components_cm1:
    for key, value in H_SO_intermediate_components_cm1.items():
        label = "used" if key.startswith("z") or key.startswith("ib") else "not used"
        print(
            f"  {key:18s} = "
            f"{value.real: .6f} {value.imag:+.6f}i cm^-1   "
            f"|H| = {abs(value):.6f}   [{label}]"
        )
else:
    print("  No intermediate SOC components were identified.")

print("\nEffective SOC from Eq. 6 / Eq. 2.21:")
print(f"  H_SO_ORCA_effective_cm = {H_SO_ORCA_effective_cm:.6f} cm^-1")

print("\nDiagnostic full-matrix norm:")
print(f"  H_SO_all_matrix_elements_cm = {H_SO_all_matrix_elements_cm:.6f} cm^-1")

# ============================================================
# ORCA SOC operator convention and optional user override
# ============================================================

section("SOC OPERATOR CONVENTION")

print("The ORCA SOC value printed above is obtained with the ORCA mean-field")
print("spin-orbit treatment requested by the %rel block, here SOCType 3 with")
print("SOCFlags 1,4,3,0 and SOCMaxCenter 4.")
print()
print("The exact Breit-Pauli spin-orbit operator contains one-electron and")
print("two-electron terms. ORCA does not evaluate the full Breit-Pauli")
print("operator directly in this workflow; instead, it uses an effective")
print("one-electron mean-field approximation that includes the dominant")
print("Coulomb and exchange contributions to the two-electron SOC operator.")
print()
print("Therefore, ORCA and GAMESS SOC values may differ if GAMESS is using a")
print("full Breit-Pauli treatment or a different two-electron SOC convention.")

SOC_EFFECTIVE_ONLY = False
SOC_MATRIX_CHANNELS_AVAILABLE = True

if ask_yes_no("\nUse a user-supplied SOC value instead of the ORCA effective SOC?", default=False):

    while True:
        H_SO_user_cm = ask_float("Enter the effective SOC value to use downstream in cm^-1: ")

        if H_SO_user_cm < 0.0:
            print("\nWARNING: SOC cannot be negative.")
            print("Please enter a non-negative effective SOC value in cm^-1.\n")
            continue

        break

    H_SO_cm = float(H_SO_user_cm)
    H_SO_source = "User-supplied effective SOC value"

    SOC_EFFECTIVE_ONLY = True
    SOC_MATRIX_CHANNELS_AVAILABLE = False

    # Disable channel/intermediate analysis because the user value is scalar only.
    SOC_Ms_matrix_cm1 = np.zeros_like(SOC_Ms_matrix_cm1, dtype=complex)
    SOC_Ms_matrix_scaled_cm1 = SOC_Ms_matrix_cm1.copy()
    SOC_Ms_abs_cm1 = np.abs(SOC_Ms_matrix_cm1)
    SOC_Ms_abs_scaled_cm1 = SOC_Ms_abs_cm1.copy()

    H_SO_channels_cm1 = {}
    H_SO_intermediate_components_cm1 = {}
    H_SO_ST_components_cm1 = {}
    H_SO_DQ_components_cm1 = {}
    H_SO_TQ_components_cm1 = {}
    H_SO_QS_components_cm1 = {}

    print(f"\nUser-supplied effective SOC accepted: H_SO_cm = {H_SO_cm:.6f} cm^-1")
    print("Downstream Steps 8–10 will run EFFECTIVE-ONLY probabilities and rates.")

else:
    H_SO_user_cm = None

    if abs(float(H_SO_cm)) <= 1.0e-12 or len(H_SO_channels_cm1) == 0:
        H_SO_cm = 0.0
        H_SO_source = "Zero effective SOC from empty/nonzero-free ORCA SOC matrix"

        SOC_EFFECTIVE_ONLY = True
        SOC_MATRIX_CHANNELS_AVAILABLE = False

        H_SO_channels_cm1 = {}
        H_SO_intermediate_components_cm1 = {}
        H_SO_ST_components_cm1 = {}
        H_SO_DQ_components_cm1 = {}
        H_SO_TQ_components_cm1 = {}
        H_SO_QS_components_cm1 = {}

        print("\nORCA SOC matrix has no usable nonzero channels.")
        print("Effective SOC is set to zero.")
        print("Downstream Steps 8–10 will run EFFECTIVE-ONLY zero-SOC probabilities and rates.")

    else:
        SOC_EFFECTIVE_ONLY = False
        SOC_MATRIX_CHANNELS_AVAILABLE = True
        print(f"\nUsing ORCA effective SOC downstream: H_SO_cm = {H_SO_cm:.6f} cm^-1")

# Backward-compatible component dictionaries
H_SO_ST_components_cm1 = {}
H_SO_DQ_components_cm1 = {}
H_SO_TQ_components_cm1 = {}
H_SO_QS_components_cm1 = {}

if abs(S_low - 0.0) < 1.0e-8 and abs(S_high - 1.0) < 1.0e-8:
    H_SO_ST_components_cm1 = H_SO_intermediate_components_cm1
if abs(S_low - 0.5) < 1.0e-8 and abs(S_high - 1.5) < 1.0e-8:
    H_SO_DQ_components_cm1 = H_SO_intermediate_components_cm1
if abs(S_low - 1.0) < 1.0e-8 and abs(S_high - 2.0) < 1.0e-8:
    H_SO_TQ_components_cm1 = H_SO_intermediate_components_cm1
if abs(S_low - 1.5) < 1.0e-8 and abs(S_high - 2.5) < 1.0e-8:
    H_SO_QS_components_cm1 = H_SO_intermediate_components_cm1

# ============================================================
# Download and save outputs
# ============================================================

section("SAVING STEP 2 OUTPUTS")

# Download important remote files when available
for remote_path, local_name in [
    (remote_orb_out, orb_out_filename),
    (remote_orb_gbw, orb_gbw_filename),
    (remote_uhf_out, uhf_out_filename),
    (remote_uhf_gbw, uhf_gbw_filename),
    (remote_uhf_uno, uhf_uno_filename),
    (remote_locinp, locinp_filename),
    (remote_loc_file, loc_filename),
    (remote_soc_out, soc_out_filename),
    (remote_soc_inp, soc_inp_filename),
    (remote_orca_loc_stdout, os.path.basename(remote_orca_loc_stdout)),
]:
    try:
        sftp.get(remote_path, os.path.join(local_soc, local_name))
    except Exception:
        pass

SOC_FOLDER_REMOTE = remote_soc
SOC_OUT_REMOTE = remote_soc_out
SOC_INP_REMOTE = remote_soc_inp
SOC_GBW_REMOTE = remote_orb_gbw
SOC_LOC_REMOTE = remote_loc_file
SOC_LOCINP_REMOTE = remote_locinp
SOC_ORCA_LOC_STDOUT_REMOTE = remote_orca_loc_stdout

soc_matrix_file = os.path.join(local_soc, f"{soc_jobname}_SOC_Ms_matrix.txt")
soc_values_file = os.path.join(local_soc, f"{soc_jobname}_SOC_values.txt")
active_diag_file = os.path.join(local_soc, f"{soc_jobname}_active_space_and_protocol.txt")
loc_diag_file = os.path.join(local_soc, f"{soc_jobname}_orca_loc_output.txt")

active_diag_lines = [
    "Step 2 active-space and SOC protocol diagnostics",
    "",
    f"jobname = {jobname}",
    f"charge = {charge}",
    f"multiplicities = {multiplicities}",
    f"mult_main = {mult_main}",
    f"mult_other = {mult_other}",
    f"high_spin_ROHF_multiplicity = {soc_orbital_mult}",
    f"basis = {basis}",
    f"selected_SOC_orbital_source = {soc_orbital_source}",
    f"selected_active_orbitals = {soc_active_indices}",
    "",
    f"ROHF_GBW = {orb_gbw_filename}",
    f"ROHF_OUT = {orb_out_filename}",
    f"UHF_UNO_OUT = {uhf_out_filename}",
    f"UHF_UNO_GBW = {uhf_gbw_filename}",
    f"UHF_UNO_FILE = {uhf_uno_filename}",
    f"locinp_file = {locinp_filename}",
    f"loc_file = {loc_filename}",
    f"orca_loc_command = {orca_loc_command}",
    "",
    f"detected_SOMOs = {rohf_somo_indices}",
    f"first_SOMO = {first_somo}",
    f"last_SOMO = {last_somo}",
    f"active_space = CAS({nel_soc},{norb_soc})",
    f"ROHF_detected_SOMOs = {rohf_somo_indices}",
    f"UHF_UNO_SOMO_like_orbitals = {uhf_uno_somo_indices}",
    f"UHF_UNO_active_orbitals = {uhf_uno_active_indices}",
    f"UHF_UNO_active_space = CAS({uhf_uno_active_electrons},{uhf_uno_active_orbitals})",
    f"metal_symbol = {metal_symbol}",
    f"metal_population_threshold = {metal_population_threshold:.6f}",
    "",
    f"selected_SOC_protocol = {soc_protocol}",
    f"selection_rationale = {soc_protocol_reason}",
    "",
    "Localized SOMO diagnostics:"
]

for item in lmo_diagnostics:
    active_diag_lines.append(
        f"MO {item['mo_index']} | metal_population = {item['metal_population']:.10f} | "
        f"dominant_atom = {item['dominant_atom_index']}{item['dominant_atom_symbol']} | "
        f"dominant_population = {item['dominant_population']:.10f} | "
        f"passes_threshold = {item['passes_threshold']}"
    )

active_diag_lines += ["", "Generated SOC input:", soc_inp_text]
write_local_text(active_diag_file, "\n".join(active_diag_lines) + "\n")
write_local_text(loc_diag_file, orca_loc_output_text)

matrix_lines = [
    "Ms-resolved intermultiplicity SOC matrix",
    "",
    f"S_low = {S_low:.6f}",
    f"S_high = {S_high:.6f}",
    "Rows = low-spin Ms values",
    " ".join(f"{x:.6f}" for x in Ms_low),
    "Columns = high-spin Ms values",
    " ".join(f"{x:.6f}" for x in Ms_high),
    "",
    "Complex SOC matrix entries as Real Imag in cm^-1:"
]
for i in range(SOC_Ms_matrix_cm1.shape[0]):
    row = []
    for j in range(SOC_Ms_matrix_cm1.shape[1]):
        value = SOC_Ms_matrix_cm1[i, j]
        row.append(f"({value.real:.10f},{value.imag:.10f})")
    matrix_lines.append(" ".join(row))

matrix_lines += ["", "Absolute SOC matrix in cm^-1:"]
for i in range(SOC_Ms_abs_cm1.shape[0]):
    matrix_lines.append(" ".join(f"{SOC_Ms_abs_cm1[i, j]:.10f}" for j in range(SOC_Ms_abs_cm1.shape[1])))

matrix_lines += ["", "Intermediate SOC components:"]
if H_SO_intermediate_components_cm1:
    for key, value in H_SO_intermediate_components_cm1.items():
        matrix_lines.append(f"{key}  {value.real:.10f}  {value.imag:.10f}  {abs(value):.10f}")
else:
    matrix_lines.append("No nonzero intermediate SOC components.")

matrix_lines += [
    "",
    "Effective SOC:",
    "Definition = sqrt(sum over all Ms and Ms_prime |<S,Ms|H_SO|S_prime,Ms_prime>|^2)",
    "No empirical factor of 2 is applied.",
    f"H_SO_ORCA_effective_cm = {H_SO_ORCA_effective_cm:.10f}",
    f"H_SO_user_cm = {H_SO_user_cm}",
    f"H_SO_cm = {H_SO_cm:.10f}",
    f"H_SO_source = {H_SO_source}",
    f"SOC_CONVENTION_LABEL = {SOC_CONVENTION_LABEL}"
]
write_local_text(soc_matrix_file, "\n".join(matrix_lines) + "\n")

soc_value_lines = [
    "Raw ORCA SOC table elements",
    "",
    "BraBlock BraRoot BraS BraMs KetBlock KetRoot KetS KetMs Real Imag Abs Type"
]
for item in soc_values:
    tag = "inter" if item["intermultiplicity"] else "same-spin"
    soc_value_lines.append(
        f"{item['bra_block']:4d} {item['bra_root']:4d} "
        f"{item['bra_S']:8.3f} {item['bra_Ms']:8.3f} "
        f"{item['ket_block']:4d} {item['ket_root']:4d} "
        f"{item['ket_S']:8.3f} {item['ket_Ms']:8.3f} "
        f"{item['real']:14.8f} {item['imag']:14.8f} "
        f"{item['abs_cm1']:14.8f} {tag}"
    )
if not soc_values:
    soc_value_lines.append("No nonzero SOC matrix elements were printed by ORCA.")
soc_value_lines += [
    "",
    f"H_SO_ORCA_effective_cm = {H_SO_ORCA_effective_cm:.10f}",
    f"H_SO_cm = {H_SO_cm:.10f}",
    f"H_SO_source = {H_SO_source}",
    f"selected_SOC_protocol = {soc_protocol}"
]
write_local_text(soc_values_file, "\n".join(soc_value_lines) + "\n")

for local_path in [soc_matrix_file, soc_values_file, active_diag_file, loc_diag_file]:
    sftp.put(local_path, posixpath.join(remote_soc, os.path.basename(local_path)))

# ============================================================
# Export variables for later steps
# ============================================================

H_SO_raw_ORCA_norm_cm = H_SO_ORCA_effective_cm
SOC_Ms_matrix_scaled_cm1 = SOC_Ms_matrix_cm1
SOC_Ms_abs_scaled_cm1 = SOC_Ms_abs_cm1

globals().update({
    "H_SO_cm": H_SO_cm,
    "H_SO_ORCA_effective_cm": H_SO_ORCA_effective_cm,
    "H_SO_user_cm": H_SO_user_cm,
    "H_SO_source": H_SO_source,
    "H_SO_intermediate_components_cm1": H_SO_intermediate_components_cm1,
    "H_SO_channels_cm1": H_SO_channels_cm1,
    "H_SO_ST_components_cm1": H_SO_ST_components_cm1,
    "H_SO_DQ_components_cm1": H_SO_DQ_components_cm1,
    "H_SO_TQ_components_cm1": H_SO_TQ_components_cm1,
    "H_SO_QS_components_cm1": H_SO_QS_components_cm1,
    "H_SO_raw_ORCA_norm_cm": H_SO_raw_ORCA_norm_cm,
    "SOC_Ms_matrix_cm1": SOC_Ms_matrix_cm1,
    "SOC_Ms_matrix_scaled_cm1": SOC_Ms_matrix_scaled_cm1,
    "SOC_EFFECTIVE_ONLY": SOC_EFFECTIVE_ONLY,
    "SOC_MATRIX_CHANNELS_AVAILABLE": SOC_MATRIX_CHANNELS_AVAILABLE,
    "SOC_Ms_abs_cm1": SOC_Ms_abs_cm1,
    "SOC_Ms_abs_scaled_cm1": SOC_Ms_abs_scaled_cm1,
    "SOC_CONVENTION_FACTOR": SOC_CONVENTION_FACTOR,
    "SOC_CONVENTION_LABEL": SOC_CONVENTION_LABEL,
    "soc_values": soc_values,
    "rohf_somo_indices": rohf_somo_indices,
    "uno_occupations": uno_occupations,
    "uhf_uno_somo_indices": uhf_uno_somo_indices,
    "uhf_uno_active_indices": uhf_uno_active_indices,
    "uhf_uno_active_electrons": uhf_uno_active_electrons,
    "uhf_uno_active_orbitals": uhf_uno_active_orbitals,
    "soc_orbital_source": soc_orbital_source,
    "soc_active_indices": soc_active_indices,
    "soc_nel": nel_soc,
    "soc_norb": norb_soc,
    "soc_mults": soc_mults,
    "soc_protocol": soc_protocol,
    "soc_protocol_reason": soc_protocol_reason,
    "lmo_diagnostics": lmo_diagnostics,
    "metal_symbol": metal_symbol,
    "metal_population_threshold": metal_population_threshold,
    "SOC_FOLDER_REMOTE": SOC_FOLDER_REMOTE,
    "SOC_OUT_REMOTE": SOC_OUT_REMOTE,
    "SOC_INP_REMOTE": SOC_INP_REMOTE,
    "SOC_GBW_REMOTE": SOC_GBW_REMOTE,
    "SOC_LOC_REMOTE": SOC_LOC_REMOTE,
    "SOC_LOCINP_REMOTE": SOC_LOCINP_REMOTE,
    "SOC_ORCA_LOC_STDOUT_REMOTE": SOC_ORCA_LOC_STDOUT_REMOTE,
    "soc_matrix_file": soc_matrix_file,
    "soc_values_file": soc_values_file,
    "active_diag_file": active_diag_file,
    "loc_diag_file": loc_diag_file
})

# ============================================================
# Final summary
# ============================================================

section("STEP 2 SUMMARY")

print(f"Detected ROHF SOMOs            : {rohf_somo_indices}")
print(f"Detected UHF/UNO active orbs   : {uhf_uno_active_indices}")
print(f"Selected SOC orbital source    : {soc_orbital_source}")
print(f"Selected active space          : CAS({nel_soc},{norb_soc})")
print(f"Detected metal                 : {metal_symbol}")
print(f"Selected SOC protocol          : {soc_protocol}")
print(f"Effective ORCA SOC             : {H_SO_ORCA_effective_cm:.6f} cm^-1")
print(f"Final SOC used downstream      : {H_SO_cm:.6f} cm^-1")
print(f"SOC source                     : {H_SO_source}")

print("\nImportant variables available for later steps:")
print("  H_SO_cm")
print("  H_SO_ORCA_effective_cm")
print("  H_SO_user_cm")
print("  H_SO_source")
print("  H_SO_intermediate_components_cm1")
print("  SOC_EFFECTIVE_ONLY")
print("  SOC_MATRIX_CHANNELS_AVAILABLE")
print("  H_SO_channels_cm1")
print("  SOC_Ms_matrix_cm1")
print("  SOC_Ms_abs_cm1")
print("  soc_protocol")
print("  rohf_somo_indices")
print("  uhf_uno_somo_indices, uhf_uno_active_indices")
print("  soc_orbital_source, soc_active_indices")
print("  soc_nel, soc_norb")
print("  SOC_FOLDER_REMOTE, SOC_OUT_REMOTE, SOC_INP_REMOTE")
print("  SOC_GBW_REMOTE, SOC_LOC_REMOTE, SOC_LOCINP_REMOTE")

print("\nSTEP 2 COMPLETED SUCCESSFULLY.\n")





#%% STEP 3. CLUSTER DFT ENGRADIENTS AT MECP

import os
import posixpath
import numpy as np

print(r'''
====================================================================
 STEP 3 | CLUSTER VERSION
 DFT Engrad calculations and gradient-difference extraction at MECP
====================================================================

This step uses the optimized MECP geometry from Step 1 and computes
DFT analytical gradients for the two spin surfaces. These gradients are
used to define the Landau-Zener gradient-difference vector at the MECP.

The gradient protocol follows the DFT/PBE-style surface-gradient
treatment used for the Landau-Zener model. These are not CASSCF
gradients and are independent of the SOC active-space protocol selected
in Step 2.

Both spin-state Engrad calculations are submitted to the remote HPC
system simultaneously and monitored together.
''')

# ============================================================
# Required variables from previous steps
# ============================================================

required_vars_step3 = [
    "jobname",
    "MECP_geometry",
    "charge",
    "method",
    "basis",
    "nprocs",
    "mem_gb",
    "maxcore_mb",
    "multiplicities",
    "mult_main",
    "mult_other",
    "ssh",
    "sftp",
    "remote_base",
    "local_base",
    "remote_mkdir_p",
    "remote_file_exists",
    "remote_read_text",
    "run_ssh",
    "submit_orca_job_no_monitor",
    "monitor_submitted_orca_jobs"
]

for var in required_vars_step3:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. "
            "Run cluster Steps 1 and 2 first."
        )

workflow_mode = "MECP_ONLY"

if not isinstance(MECP_geometry, str) or not MECP_geometry.strip():
    raise RuntimeError("MECP_geometry is empty. Run Step 1 first.")

# Step 3 reuses the cluster bash and submission settings selected in Step 1.
# No bash-template or submission-command review is performed in Step 3.

if "CLUSTER_SH_TEMPLATE" not in globals():
    raise RuntimeError("CLUSTER_SH_TEMPLATE is missing. Run Step 1 first.")

if "CLUSTER_SUBMIT_COMMAND_PREFIX" not in globals():
    if "CLUSTER_SUBMIT_COMMAND_TEMPLATE" in globals():
        CLUSTER_SUBMIT_COMMAND_PREFIX = (
            CLUSTER_SUBMIT_COMMAND_TEMPLATE
            .replace("{sh_filename}", "")
            .strip()
        )
    else:
        raise RuntimeError("Cluster submission command is missing. Run Step 1 first.")

CLUSTER_SH_TEMPLATE_WAS_REVIEWED = True
CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = True

# ============================================================
# User-interface and utility helpers
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def subsection(title):
    print("\n" + "-" * 72)
    print(title)
    print("-" * 72 + "\n")


def multiplicity_name(mult):
    return {
        1: "singlet",
        2: "doublet",
        3: "triplet",
        4: "quartet",
        5: "quintet",
        6: "sextet",
        7: "septet",
        8: "octet",
        9: "nonet",
        10: "decet"
    }.get(int(mult), f"mult{mult}")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def clean_method_for_engrad(method_text):
    excluded = {
        "surfcrossopt",
        "surfcrossnumfreq",
        "numfreq",
        "freq",
        "opt",
        "engrad"
    }

    tokens = str(method_text).split()
    clean_tokens = [
        token for token in tokens
        if token.lower() not in excluded
    ]

    return " ".join(clean_tokens).strip()


def read_remote_text_file(remote_path):
    return remote_read_text(sftp, remote_path)


def extract_gradient_from_engrad_text(engrad_text, source_label="engrad"):
    lines = engrad_text.splitlines()

    natoms = None

    for i, line in enumerate(lines):
        if "Number of atoms" in line:
            for j in range(i + 1, min(i + 8, len(lines))):
                stripped = lines[j].strip()
                if stripped and not stripped.startswith("#"):
                    natoms = int(stripped)
                    break
            break

    if natoms is None:
        raise RuntimeError(f"Could not read number of atoms from {source_label}.")

    grad_start = None

    for i, line in enumerate(lines):
        if "The current gradient in Eh/bohr" in line:
            grad_start = i + 1
            break

    if grad_start is None:
        raise RuntimeError(f"Could not find gradient section in {source_label}.")

    gradient_values = []

    for line in lines[grad_start:]:
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#"):
            if gradient_values:
                break
            continue

        try:
            gradient_values.append(float(stripped.split()[0]))
        except ValueError:
            if gradient_values:
                break

        if len(gradient_values) == 3 * natoms:
            break

    expected = 3 * natoms

    if len(gradient_values) != expected:
        raise RuntimeError(
            f"Expected {expected} gradient values from {source_label}, "
            f"but extracted {len(gradient_values)}."
        )

    return np.array(gradient_values, dtype=float), natoms


def remote_find_engrad_file(remote_dir, jobname_i):
    expected = posixpath.join(remote_dir, f"{jobname_i}.engrad")

    if remote_file_exists(sftp, expected):
        return expected

    cmd = (
        f'cd "{remote_dir}" && '
        f'ls {jobname_i}*.engrad 2>/dev/null | head -n 1'
    )

    out, _ = run_ssh(ssh, cmd)
    detected = out.strip()

    if not detected:
        raise RuntimeError(f"No .engrad file was found for {jobname_i} in {remote_dir}.")

    return posixpath.join(remote_dir, detected)


def download_remote_file_if_present(remote_path, local_path):
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        sftp.get(remote_path, local_path)
        return True
    except Exception:
        return False




# ============================================================
# DFT settings propagation helpers from Step 1
# ============================================================

if "split_orca_xyz_block" not in globals():
    def split_orca_xyz_block(inp_text):
        lines = inp_text.splitlines()
        xyz_start = None
        for i, line in enumerate(lines):
            if line.strip().lower().startswith("* xyz"):
                xyz_start = i
                break
        if xyz_start is None:
            raise RuntimeError("Could not find '* xyz charge multiplicity' block.")
        xyz_end = None
        for j in range(xyz_start + 1, len(lines)):
            if lines[j].strip() == "*":
                xyz_end = j
                break
        if xyz_end is None:
            raise RuntimeError("Could not find closing '*' for xyz block.")
        pre_xyz = "\n".join(lines[:xyz_start]).rstrip() + "\n"
        xyz_block = "\n".join(lines[xyz_start:xyz_end + 1]).rstrip() + "\n"
        post_xyz = "\n".join(lines[xyz_end + 1:]).rstrip()
        return pre_xyz, xyz_block, post_xyz

if "extract_orca_percent_blocks_from_pre" not in globals():
    def extract_orca_percent_blocks_from_pre(pre_text, exclude_names=None):
        exclude_names = {str(x).lower() for x in (exclude_names or [])}
        lines = str(pre_text).splitlines()
        blocks = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped.startswith("%"):
                i += 1
                continue
            block_name = stripped[1:].split()[0].lower()
            block_lines = [line]
            i += 1
            if stripped.lower().endswith(" end") or block_name == "maxcore":
                pass
            else:
                while i < len(lines):
                    block_lines.append(lines[i])
                    if lines[i].strip().lower() == "end":
                        i += 1
                        break
                    i += 1
            if block_name not in exclude_names:
                blocks.append("\n".join(block_lines).rstrip())
        return ("\n\n".join(blocks).strip() + "\n\n") if blocks else ""

if "adapt_bang_line_for_job" not in globals():
    ORCA_JOB_KEYWORDS = {"opt", "freq", "numfreq", "engrad", "surfcrossopt", "surfcrossnumfreq", "sp"}
    def adapt_bang_line_for_job(bang_line, job_type):
        tokens = str(bang_line).strip().split()
        if not tokens or tokens[0] != "!":
            raise RuntimeError("Editable ORCA settings block must start with a ! line.")
        clean = [tok for tok in tokens[1:] if tok.lower() not in ORCA_JOB_KEYWORDS]
        job_type = str(job_type).lower()
        if job_type == "engrad":
            new_body = ["Engrad"] + clean
        elif job_type == "minimum":
            new_body = clean + ["Opt", "Freq"]
        elif job_type == "single_point":
            new_body = clean
        elif job_type == "mecp":
            new_body = clean + ["SurfCrossOpt"]
        elif job_type == "freq":
            new_body = clean + ["SurfCrossNumFreq"]
        else:
            raise RuntimeError(f"Unknown ORCA job_type: {job_type}")
        return "! " + " ".join(new_body)

if "extract_bang_line_from_pre" not in globals():
    def extract_bang_line_from_pre(pre_text):
        for line in str(pre_text).splitlines():
            if line.strip().startswith("!"):
                return line.strip()
        raise RuntimeError("Could not find ORCA ! line.")

if "make_dft_pre_xyz" not in globals():
    def make_dft_pre_xyz(job_type, job_specific_blocks=""):
        base_pre = globals().get("DFT_REVIEWED_PRE_XYZ", "")
        if not base_pre.strip():
            base_pre = f"""! {method} {basis} TightSCF SlowConv Opt Freq

%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

%scf
  MaxIter 800
  SOSCFStart 0.01
  LevelShift 0.5
end

%output
  PrintLevel 3
end
"""
        header = adapt_bang_line_for_job(extract_bang_line_from_pre(base_pre), job_type)
        blocks = globals().get("ORCA_TRANSFER_BLOCKS_ALL", "")
        if not blocks.strip():
            blocks = extract_orca_percent_blocks_from_pre(base_pre)
        text = header.rstrip() + "\n\n"
        if blocks.strip():
            text += blocks.rstrip() + "\n\n"
        if str(job_specific_blocks).strip():
            text += str(job_specific_blocks).strip() + "\n\n"
        return text.rstrip() + "\n"

if "apply_dft_settings_to_input" not in globals():
    def apply_dft_settings_to_input(inp_text, job_type, job_specific_blocks=""):
        _, xyz_block, post_xyz = split_orca_xyz_block(inp_text)
        new_pre = make_dft_pre_xyz(job_type, job_specific_blocks=job_specific_blocks)
        out = new_pre.rstrip() + "\n\n" + xyz_block
        if post_xyz.strip():
            out += "\n" + post_xyz.strip() + "\n"
        return out

# ============================================================
# Step 3 settings
# ============================================================

section("STEP 3 SETTINGS")

engrad_mults = sorted([int(mult_main), int(mult_other)])

if len(engrad_mults) != 2:
    raise RuntimeError("Step 3 expects exactly two spin multiplicities.")

engrad_method = clean_method_for_engrad(method)

if not engrad_method:
    raise RuntimeError(
        "The Engrad method became empty after removing Opt/Freq/MECP keywords."
    )

print("Workflow mode              : MECP_ONLY")
print("Gradient level             : DFT Engrad")
print(f"Method                     : {engrad_method}")
print(f"Basis set                  : {basis}")
print(f"Spin multiplicities         : {engrad_mults}")
print(f"Number of ORCA processors   : {nprocs}")
print(f"MaxCore per processor       : {maxcore_mb} MB")
print(f"Approximate total memory    : {nprocs * mem_gb} GB")

# ============================================================
# Directories and file names
# ============================================================

section("DIRECTORY PREPARATION")

remote_engrad = posixpath.join(remote_base, "Engrad")
remote_mkdir_p(sftp, remote_engrad)

local_engrad = os.path.join(local_base, "Engrad")
os.makedirs(local_engrad, exist_ok=True)

print(f"Remote Engrad directory : {remote_engrad}")
print(f"Local Engrad directory  : {local_engrad}")

# ============================================================
# Engrad input construction
# ============================================================

def make_engrad_input(mult, scf_block_text, extra_keywords="SlowConv"):
    header = f"! Engrad {engrad_method} {basis} TightSCF"

    if extra_keywords.strip():
        header += " " + extra_keywords.strip()

    inp = f'''{header}

%pal nprocs {nprocs} end
%maxcore {maxcore_mb}

{scf_block_text}

%output
  PrintLevel 3
end

* xyz {charge} {mult}
{MECP_geometry}
*
'''

    return apply_dft_settings_to_input(
        inp,
        job_type="engrad"
    )


engrad_attempt_settings = [
    {
        "attempt": 1,
        "extra_keywords": "SlowConv",
        "scf": '''%scf
  MaxIter 800
  SOSCFStart 1
  LevelShift 0.5
end'''
    }
]

# ============================================================
# Submit both Engrad jobs simultaneously
# ============================================================

section("SUBMITTING DFT ENGRAD JOBS")

engrad_job_definitions = []

for mult in engrad_mults:
    spin_name = multiplicity_name(mult)
    engrad_jobname = f"{jobname}_Engrad_{spin_name}"

    inp_text = make_engrad_input(
        mult=mult,
        scf_block_text=engrad_attempt_settings[0]["scf"],
        extra_keywords=engrad_attempt_settings[0]["extra_keywords"]
    )

    local_inp_path = os.path.join(local_engrad, f"{engrad_jobname}.inp")
    write_local_text(local_inp_path, inp_text)

    engrad_job_definitions.append({
        "mult": mult,
        "spin_name": spin_name,
        "jobname": engrad_jobname,
        "inp_text": inp_text,
        "job_label": f"{spin_name} DFT Engrad at MECP"
    })

    subsection(f"Generated ORCA Engrad input | {engrad_jobname}.inp")
    print(inp_text)

submitted_engrad_jobs = []

for job in engrad_job_definitions:
    job_record = submit_orca_job_no_monitor(
        remote_engrad,
        job["jobname"],
        job["inp_text"],
        job["job_label"],
        attempt=1
    )

    job_record["mult"] = job["mult"]
    job_record["spin_name"] = job["spin_name"]

    job_record["repair_policy"] = "engrad_restricted"
    job_record["locked"] = {
        "method": engrad_method,
        "basis": basis,
        "charge": charge,
        "mult": job["mult"],
        "geom": MECP_geometry,
    }

    submitted_engrad_jobs.append(job_record)

print("\nBoth spin-state Engrad jobs have been submitted.")
print("The two calculations will now be monitored simultaneously.\n")

submitted_engrad_jobs = monitor_submitted_orca_jobs(
    remote_engrad,
    submitted_engrad_jobs,
    allow_interactive_repair=True
)

for job_record in submitted_engrad_jobs:
    if job_record["status"] != "OK":
        raise SystemExit(
            f"{job_record['jobname']} did not complete successfully."
        )

# ============================================================
# Extract gradients from remote .engrad files
# ============================================================

section("EXTRACTING GRADIENTS FROM ENGRAD FILES")

gradients_by_multiplicity = {}
gradients_by_name = {}
natoms_by_multiplicity = {}
engrad_files_remote = {}

for job_record in submitted_engrad_jobs:
    mult = int(job_record["mult"])
    spin_name = job_record["spin_name"]
    engrad_jobname = job_record["jobname"]

    remote_engrad_file = remote_find_engrad_file(remote_engrad, engrad_jobname)
    engrad_files_remote[mult] = remote_engrad_file

    engrad_text = read_remote_text_file(remote_engrad_file)
    gradient, natoms = extract_gradient_from_engrad_text(
        engrad_text,
        source_label=remote_engrad_file
    )

    gradients_by_multiplicity[mult] = gradient
    gradients_by_name[spin_name] = gradient
    natoms_by_multiplicity[mult] = natoms

    globals()[f"g_{spin_name}"] = gradient

    local_engrad_file = os.path.join(local_engrad, posixpath.basename(remote_engrad_file))
    write_local_text(local_engrad_file, engrad_text)

    for ext in ["inp", "out", "gbw"]:
        remote_path = posixpath.join(remote_engrad, f"{engrad_jobname}.{ext}")
        local_path = os.path.join(local_engrad, f"{engrad_jobname}.{ext}")
        download_remote_file_if_present(remote_path, local_path)

    print(f"g_{spin_name} extracted from {posixpath.basename(remote_engrad_file)}")
    print(f"  multiplicity = {mult}")
    print(f"  atoms        = {natoms}")
    print(f"  gradient len = {gradient.size}")
    print(f"  first 5 vals = {gradient[:5]}\n")

# ============================================================
# Define low-spin and high-spin gradients
# ============================================================

section("LANDAU-ZENER GRADIENT DEFINITIONS")

available_mults = sorted(gradients_by_multiplicity.keys())

if len(available_mults) < 2:
    raise RuntimeError("Need two spin-surface gradients for Landau-Zener analysis.")

mult_LS = available_mults[0]
mult_HS = available_mults[-1]

spin_name_LS = multiplicity_name(mult_LS)
spin_name_HS = multiplicity_name(mult_HS)

g_LS = gradients_by_multiplicity[mult_LS]
g_HS = gradients_by_multiplicity[mult_HS]

if g_LS.shape != g_HS.shape:
    raise RuntimeError(
        f"Gradient size mismatch: g_LS shape {g_LS.shape}, "
        f"g_HS shape {g_HS.shape}."
    )

delta_g_cart = g_HS - g_LS

DeltaG_norm_Eh_per_Bohr = float(np.linalg.norm(delta_g_cart))

if not np.isfinite(DeltaG_norm_Eh_per_Bohr) or DeltaG_norm_Eh_per_Bohr <= 0.0:
    raise RuntimeError("Invalid gradient-difference norm. Check Engrad outputs.")

g_LS_norm_Eh_per_Bohr = float(np.linalg.norm(g_LS))
g_HS_norm_Eh_per_Bohr = float(np.linalg.norm(g_HS))

MECP_seam_normal_cart = delta_g_cart / DeltaG_norm_Eh_per_Bohr

n_cart = MECP_seam_normal_cart
DELTAF_PARALLEL_EH_PER_BOHR = DeltaG_norm_Eh_per_Bohr
gradmean_Eh_per_Bohr = float(
    np.sqrt(g_LS_norm_Eh_per_Bohr * g_HS_norm_Eh_per_Bohr)
)

print(f"Low-spin surface              : {spin_name_LS}, multiplicity {mult_LS}")
print(f"High-spin surface             : {spin_name_HS}, multiplicity {mult_HS}")

print("\nMECP gradient diagnostics:")
print(f"  ||g_LS||                         = {g_LS_norm_Eh_per_Bohr:.12e} Eh/Bohr")
print(f"  ||g_HS||                         = {g_HS_norm_Eh_per_Bohr:.12e} Eh/Bohr")
print(f"  sqrt(||g_LS||*||g_HS||)           = {gradmean_Eh_per_Bohr:.12e} Eh/Bohr")
print(f"  ||g_HS - g_LS||                  = {DeltaG_norm_Eh_per_Bohr:.12e} Eh/Bohr")
print(f"  DELTAF_PARALLEL_EH_PER_BOHR      = {DELTAF_PARALLEL_EH_PER_BOHR:.12e} Eh/Bohr")
print(f"  MECP seam-normal norm            = {np.linalg.norm(MECP_seam_normal_cart):.8f}")

# ============================================================
# Save gradient summary
# ============================================================

section("SAVING STEP 3 OUTPUTS")

local_grad_file = os.path.join(
    local_engrad,
    f"{jobname}_Engrad_gradients_summary.txt"
)

summary_lines = []
summary_lines.append("DFT Engrad gradients extracted at MECP")
summary_lines.append("Workflow mode = MECP_ONLY")
summary_lines.append("Run mode = CLUSTER")
summary_lines.append(f"Engrad method = {engrad_method}")
summary_lines.append(f"Basis = {basis}")
summary_lines.append(f"Charge = {charge}")
summary_lines.append(f"Multiplicities = {engrad_mults}")
summary_lines.append("")
summary_lines.append(f"Low-spin multiplicity  = {mult_LS} ({spin_name_LS})")
summary_lines.append(f"High-spin multiplicity = {mult_HS} ({spin_name_HS})")
summary_lines.append("")
summary_lines.append(f"||g_LS||        = {g_LS_norm_Eh_per_Bohr:.12e} Eh/Bohr")
summary_lines.append(f"||g_HS||        = {g_HS_norm_Eh_per_Bohr:.12e} Eh/Bohr")
summary_lines.append(f"gradmean        = {gradmean_Eh_per_Bohr:.12e} Eh/Bohr")
summary_lines.append(f"||g_HS-g_LS||   = {DeltaG_norm_Eh_per_Bohr:.12e} Eh/Bohr")
summary_lines.append(f"DeltaF_parallel = {DELTAF_PARALLEL_EH_PER_BOHR:.12e} Eh/Bohr")
summary_lines.append("")
summary_lines.append("Remote Engrad files:")
for mult in available_mults:
    summary_lines.append(f"  mult {mult}: {engrad_files_remote[mult]}")

for spin_name, gradient in gradients_by_name.items():
    summary_lines.append("")
    summary_lines.append(f"# g_{spin_name}")
    for value in gradient:
        summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# delta_g_cart = g_HS - g_LS")
for value in delta_g_cart:
    summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# MECP_seam_normal_cart = delta_g_cart / ||delta_g_cart||")
for value in MECP_seam_normal_cart:
    summary_lines.append(f"{value: .12f}")

write_local_text(local_grad_file, "\n".join(summary_lines) + "\n")

remote_grad_file = posixpath.join(remote_engrad, posixpath.basename(local_grad_file))
sftp.put(local_grad_file, remote_grad_file)

# ============================================================
# Export variables for later steps
# ============================================================

globals().update({
    "remote_engrad": remote_engrad,
    "local_engrad": local_engrad,
    "engrad_method": engrad_method,
    "engrad_mults": engrad_mults,
    "engrad_job_records": submitted_engrad_jobs,
    "gradients_by_multiplicity": gradients_by_multiplicity,
    "gradients_by_name": gradients_by_name,
    "natoms_by_multiplicity": natoms_by_multiplicity,
    "engrad_files_remote": engrad_files_remote,
    "mult_LS": mult_LS,
    "mult_HS": mult_HS,
    "spin_name_LS": spin_name_LS,
    "spin_name_HS": spin_name_HS,
    "g_LS": g_LS,
    "g_HS": g_HS,
    "delta_g_cart": delta_g_cart,
    "MECP_seam_normal_cart": MECP_seam_normal_cart,
    "n_cart": n_cart,
    "DeltaG_norm_Eh_per_Bohr": DeltaG_norm_Eh_per_Bohr,
    "DELTAF_PARALLEL_EH_PER_BOHR": DELTAF_PARALLEL_EH_PER_BOHR,
    "gradmean_Eh_per_Bohr": gradmean_Eh_per_Bohr,
    "local_grad_file": local_grad_file,
    "remote_grad_file": remote_grad_file
})

# ============================================================
# Final summary
# ============================================================

section("STEP 3 SUMMARY")

print(f"Engrad method                 : {engrad_method}")
print(f"Basis                         : {basis}")
print(f"Low-spin surface              : {spin_name_LS}, multiplicity {mult_LS}")
print(f"High-spin surface             : {spin_name_HS}, multiplicity {mult_HS}")
print(f"DELTAF_PARALLEL_EH_PER_BOHR   : {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
print(f"Gradient summary, local       : {local_grad_file}")
print(f"Gradient summary, remote      : {remote_grad_file}")

print("\nImportant variables available for later steps:")
print("  g_LS")
print("  g_HS")
print("  delta_g_cart")
print("  MECP_seam_normal_cart")
print("  DELTAF_PARALLEL_EH_PER_BOHR")
print("  gradmean_Eh_per_Bohr")
print("  n_cart")
print("  gradients_by_multiplicity")
print("  gradients_by_name")
print("  local_grad_file")
print("  remote_grad_file")

print("\nSTEP 3 COMPLETED SUCCESSFULLY.\n")



#%% STEP 4. CLUSTER NAST-STYLE MECP GRADIENT AND REACTION-COORDINATE DEFINITIONS

import os
import posixpath
import numpy as np

print(r'''
====================================================================
 STEP 4 | CLUSTER VERSION
 MECP Cartesian gradient definitions and reaction-coordinate vector
====================================================================

This step uses the two DFT Engrad gradients extracted at the MECP in
Step 3. No additional ORCA calculation is performed here.

The gradient-difference vector is defined in the NAST/effhess convention
as

    deltaG_NAST = g_LS - g_HS

and its Cartesian magnitude is

    DELTAF_PARALLEL_EH_PER_BOHR = ||g_LS - g_HS||

The quantity

    CARTESIAN_GRADMEAN_EH_PER_BOHR
        = sqrt(||g_LS|| * ||g_HS||)

is retained only as a Cartesian-gradient diagnostic. It is not the final
reaction-coordinate-projected NAST gradmean. The authoritative projected
GRADMEAN_EH_PER_BOHR is calculated later from the effective-Hessian
reaction coordinate.

A backward-compatible direction, n_cart = g_HS - g_LS, is also retained
because later sections of the workflow may use that sign convention.
''')

# ============================================================
# Required variables from Steps 1–3
# ============================================================

required_vars_step4 = [
    "jobname",
    "MECP_geometry",
    "Reference_geometry",
    "g_LS",
    "g_HS",
    "mult_LS",
    "mult_HS",
    "spin_name_LS",
    "spin_name_HS",
    "local_base",
    "remote_base"
]

for var in required_vars_step4:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Steps 1–3 first."
        )

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = "MECP_ONLY"

# Prevent a stale projected gradmean from an earlier run from being
# mistaken for a Step 4 result. The final projected value is created
# later by the effective-Hessian/reaction-coordinate step.
globals().pop("GRADMEAN_EH_PER_BOHR", None)
globals().pop("gradmean_Eh_per_Bohr", None)

# ============================================================
# Helper functions
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ============================================================
# Gradient preparation
# ============================================================

section("MECP CARTESIAN GRADIENT DEFINITIONS")

g_LS = np.asarray(g_LS, dtype=float).reshape(-1)
g_HS = np.asarray(g_HS, dtype=float).reshape(-1)

if g_LS.shape != g_HS.shape:
    raise RuntimeError(
        f"g_LS and g_HS have different sizes: {g_LS.shape} vs {g_HS.shape}"
    )

# NAST / effhess sign convention
deltaG_LS_minus_HS = g_LS - g_HS
deltaG_cart_NAST = deltaG_LS_minus_HS

# Backward-compatible opposite convention
deltaG_HS_minus_LS = g_HS - g_LS
delta_g_cart = deltaG_HS_minus_LS

norm_deltaG = float(np.linalg.norm(deltaG_LS_minus_HS))

if not np.isfinite(norm_deltaG) or norm_deltaG <= 0.0:
    raise RuntimeError("Invalid ||g_LS - g_HS||. Check Step 3 Engrad outputs.")

DeltaF_parallel = norm_deltaG
DELTAF_PARALLEL_EH_PER_BOHR = norm_deltaG

norm_grad_LS = float(np.linalg.norm(g_LS))
norm_grad_HS = float(np.linalg.norm(g_HS))

g_LS_norm_Eh_per_Bohr = norm_grad_LS
g_HS_norm_Eh_per_Bohr = norm_grad_HS

# Cartesian diagnostic only:
# This is not the final reaction-coordinate-projected NAST gradmean.
cartesian_gradmean_Eh_per_Bohr = float(
    np.sqrt(norm_grad_LS * norm_grad_HS)
)
CARTESIAN_GRADMEAN_EH_PER_BOHR = cartesian_gradmean_Eh_per_Bohr

gradslope = float(np.dot(g_LS, g_HS))

if gradslope < 0.0:
    intersection_type = "peaked"
else:
    intersection_type = "sloped"

lambda_effhess = float(
    np.dot(deltaG_LS_minus_HS, g_LS) / (norm_deltaG ** 2)
)

# Unit vectors
n_cart_NAST = deltaG_LS_minus_HS / norm_deltaG
n_cart = deltaG_HS_minus_LS / norm_deltaG

MECP_seam_normal_cart = n_cart.copy()
reaction_direction_cart = n_cart.copy()

# Disable non-MECP/IRC variables for compatibility
IRC_frames = None
IRC_energies_hartree = None
coords_all = None
irc = None
symbols_irc = None
TS_geometry = None
irc_tangent = None
TS_index = None

print("MECP-only workflow confirmed.")
print("No TS geometry, IRC path, or IRC tangent is used in this workflow.")

print("\nSelected spin surfaces:")
print(f"  Low-spin surface   : {spin_name_LS}, multiplicity {mult_LS}")
print(f"  High-spin surface  : {spin_name_HS}, multiplicity {mult_HS}")

print("\nCartesian gradient and effhess definitions:")
print("  deltaG_NAST             = g_LS - g_HS")
print("  grad                     = ||deltaG_NAST||")
print("  Cartesian gradient mean = sqrt(||g_LS|| * ||g_HS||)")
print("  lambda                   = dot(deltaG_NAST, g_LS) / ||deltaG_NAST||^2")
print(
    "  NOTE: The final reaction-coordinate-projected GRADMEAN_EH_PER_BOHR "
    "is calculated later."
)

print("\nGradient diagnostics:")
print(f"  ||g_LS||                              = {norm_grad_LS:.12e} Eh/Bohr")
print(f"  ||g_HS||                              = {norm_grad_HS:.12e} Eh/Bohr")
print(f"  g_LS · g_HS                           = {gradslope:.12e}")
print(f"  intersection_type                     = {intersection_type}")
print(f"  DELTAF_PARALLEL_EH_PER_BOHR           = {DELTAF_PARALLEL_EH_PER_BOHR:.12e} Eh/Bohr")
print(f"  cartesian_gradmean_Eh_per_Bohr        = {cartesian_gradmean_Eh_per_Bohr:.12e} Eh/Bohr")
print(f"  lambda_effhess                        = {lambda_effhess:.12e}")
print(f"  ||n_cart_NAST||                       = {np.linalg.norm(n_cart_NAST):.8f}")
print(f"  ||n_cart||                            = {np.linalg.norm(n_cart):.8f}")

# ============================================================
# Save Step 4 outputs
# ============================================================

section("SAVING STEP 4 OUTPUTS")

local_step4 = os.path.join(local_base, "NAST_gradient_definitions")
os.makedirs(local_step4, exist_ok=True)

remote_step4 = posixpath.join(remote_base, "NAST_gradient_definitions")

if "remote_mkdir_p" in globals() and "sftp" in globals():
    remote_mkdir_p(sftp, remote_step4)

local_step4_file = os.path.join(
    local_step4,
    f"{jobname}_NAST_gradient_definitions.txt"
)

summary_lines = []

summary_lines.append("MECP Cartesian gradient and reaction-coordinate definitions")
summary_lines.append("Workflow mode = MECP_ONLY")
summary_lines.append(f"Run mode = {RUN_MODE}")
summary_lines.append("")
summary_lines.append(f"Low-spin multiplicity  = {mult_LS} ({spin_name_LS})")
summary_lines.append(f"High-spin multiplicity = {mult_HS} ({spin_name_HS})")
summary_lines.append("")
summary_lines.append("Definitions:")
summary_lines.append("deltaG_NAST = g_LS - g_HS")
summary_lines.append("delta_g_cart = g_HS - g_LS")
summary_lines.append("DELTAF_PARALLEL_EH_PER_BOHR = ||g_LS - g_HS||")
summary_lines.append(
    "CARTESIAN_GRADMEAN_EH_PER_BOHR = sqrt(||g_LS|| * ||g_HS||)"
)
summary_lines.append(
    "The final reaction-coordinate-projected GRADMEAN_EH_PER_BOHR "
    "is calculated later and is not defined in Step 4."
)
summary_lines.append("")
summary_lines.append(f"||g_LS|| = {norm_grad_LS:.12e} Eh/Bohr")
summary_lines.append(f"||g_HS|| = {norm_grad_HS:.12e} Eh/Bohr")
summary_lines.append(f"g_LS_dot_g_HS = {gradslope:.12e}")
summary_lines.append(f"intersection_type = {intersection_type}")
summary_lines.append(
    f"DELTAF_PARALLEL_EH_PER_BOHR = "
    f"{DELTAF_PARALLEL_EH_PER_BOHR:.12e} Eh/Bohr"
)
summary_lines.append(
    f"cartesian_gradmean_Eh_per_Bohr = "
    f"{cartesian_gradmean_Eh_per_Bohr:.12e} Eh/Bohr"
)
summary_lines.append(
    f"CARTESIAN_GRADMEAN_EH_PER_BOHR = "
    f"{CARTESIAN_GRADMEAN_EH_PER_BOHR:.12e} Eh/Bohr"
)
summary_lines.append(f"lambda_effhess = {lambda_effhess:.12e}")
summary_lines.append("")

summary_lines.append("# g_LS")
for value in g_LS:
    summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# g_HS")
for value in g_HS:
    summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# deltaG_cart_NAST = g_LS - g_HS")
for value in deltaG_cart_NAST:
    summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# delta_g_cart = g_HS - g_LS")
for value in delta_g_cart:
    summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# n_cart_NAST")
for value in n_cart_NAST:
    summary_lines.append(f"{value: .12f}")

summary_lines.append("")
summary_lines.append("# n_cart")
for value in n_cart:
    summary_lines.append(f"{value: .12f}")

write_local_text(local_step4_file, "\n".join(summary_lines) + "\n")

remote_step4_file = posixpath.join(
    remote_step4,
    posixpath.basename(local_step4_file)
)

if "sftp" in globals():
    try:
        sftp.put(local_step4_file, remote_step4_file)
    except Exception:
        remote_step4_file = None

print(f"Local Step 4 summary  : {local_step4_file}")
if remote_step4_file:
    print(f"Remote Step 4 summary : {remote_step4_file}")

# ============================================================
# Export variables for later steps
# ============================================================

globals().update({
    "RUN_MODE": RUN_MODE,
    "workflow_mode": workflow_mode,
    "g_LS": g_LS,
    "g_HS": g_HS,
    "g_LS_norm_Eh_per_Bohr": g_LS_norm_Eh_per_Bohr,
    "g_HS_norm_Eh_per_Bohr": g_HS_norm_Eh_per_Bohr,
    "deltaG_LS_minus_HS": deltaG_LS_minus_HS,
    "deltaG_HS_minus_LS": deltaG_HS_minus_LS,
    "deltaG_cart_NAST": deltaG_cart_NAST,
    "delta_g_cart": delta_g_cart,
    "norm_deltaG": norm_deltaG,
    "DeltaF_parallel": DeltaF_parallel,
    "DELTAF_PARALLEL_EH_PER_BOHR": DELTAF_PARALLEL_EH_PER_BOHR,
    "cartesian_gradmean_Eh_per_Bohr": cartesian_gradmean_Eh_per_Bohr,
    "CARTESIAN_GRADMEAN_EH_PER_BOHR": CARTESIAN_GRADMEAN_EH_PER_BOHR,
    "gradslope": gradslope,
    "intersection_type": intersection_type,
    "lambda_effhess": lambda_effhess,
    "n_cart_NAST": n_cart_NAST,
    "n_cart": n_cart,
    "MECP_seam_normal_cart": MECP_seam_normal_cart,
    "reaction_direction_cart": reaction_direction_cart,
    "IRC_frames": IRC_frames,
    "IRC_energies_hartree": IRC_energies_hartree,
    "coords_all": coords_all,
    "irc": irc,
    "symbols_irc": symbols_irc,
    "TS_geometry": TS_geometry,
    "irc_tangent": irc_tangent,
    "TS_index": TS_index,
    "local_step4": local_step4,
    "remote_step4": remote_step4,
    "local_step4_file": local_step4_file,
    "remote_step4_file": remote_step4_file
})

# ============================================================
# Final summary
# ============================================================

section("STEP 4 SUMMARY")

print(f"Low-spin surface                      : {spin_name_LS}, multiplicity {mult_LS}")
print(f"High-spin surface                     : {spin_name_HS}, multiplicity {mult_HS}")
print(f"Intersection type                     : {intersection_type}")
print(f"DELTAF_PARALLEL_EH_PER_BOHR           : {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
print(f"cartesian_gradmean_Eh_per_Bohr        : {cartesian_gradmean_Eh_per_Bohr:.12e}")
print(f"lambda_effhess                        : {lambda_effhess:.12e}")

print("\nImportant variables available for later steps:")
print("  deltaG_cart_NAST")
print("  delta_g_cart")
print("  n_cart_NAST")
print("  n_cart")
print("  MECP_seam_normal_cart")
print("  reaction_direction_cart")
print("  DELTAF_PARALLEL_EH_PER_BOHR")
print("  DeltaF_parallel")
print("  cartesian_gradmean_Eh_per_Bohr")
print("  CARTESIAN_GRADMEAN_EH_PER_BOHR")
print("  lambda_effhess")
print("  intersection_type")

print(
    "\nGRADMEAN_EH_PER_BOHR is intentionally not defined in Step 4. "
    "The final projected NAST value is calculated later."
)

print("\nSTEP 4 COMPLETED SUCCESSFULLY.\n")





#%% STEP 5. CLUSTER NAST-STYLE REFERENCE DATA + MECP HESSIAN JOBS

import os
import re
import posixpath
import numpy as np

print(r'''
====================================================================
 STEP 5 | CLUSTER VERSION
 NAST-style reference data and MECP Hessian generation
====================================================================

This step prepares the frequency and rotational data required for the
NAST-style density-of-states and effective-Hessian treatment.

Reference/reactant vibrational data are reused from the Step 1 optimized
minimum. MECP Hessians are generated using two SurfCrossNumFreq jobs:

  1. mult_main  as the xyz multiplicity, mult_other as the MECP partner
  2. mult_other as the xyz multiplicity, mult_main  as the MECP partner

The raw ORCA PES2 frequencies from SurfCrossNumFreq are stored only as
diagnostics. The final MECP effective frequencies used in NAST are
generated later from the projected effective Hessian.
''')

hartree_to_cm = 219474.6313705

# ============================================================
# Required variables from Steps 1–4
# ============================================================

required_vars_step5 = [
    "jobname",
    "MECP_geometry",
    "Reference_multiplicity",
    "minima_data",
    "local_base",
    "remote_base",
    "method",
    "basis",
    "charge",
    "mult_main",
    "mult_other",
    "nprocs",
    "maxcore_mb",
    "ssh",
    "sftp",
    "remote_mkdir_p",
    "remote_file_exists",
    "remote_read_text",
    "run_ssh",
    "submit_orca_job_no_monitor",
    "monitor_submitted_orca_jobs"
]

for var in required_vars_step5:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Steps 1–4 first."
        )

workflow_mode = "MECP_ONLY"
RUN_MODE = globals().get("RUN_MODE", "CLUSTER")

# Step 5 reuses Step 1 cluster submission settings.
CLUSTER_SH_TEMPLATE_WAS_REVIEWED = True
CLUSTER_SUBMIT_COMMAND_TEMPLATE_WAS_REVIEWED = True

# ============================================================
# Helpers
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def subsection(title):
    print("\n" + "-" * 72)
    print(title)
    print("-" * 72 + "\n")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_remote_text_file(remote_path):
    return remote_read_text(sftp, remote_path)


def clean_method_for_frequency(method_text):
    excluded = {
        "surfcrossopt",
        "surfcrossnumfreq",
        "numfreq",
        "freq",
        "opt",
        "engrad"
    }

    tokens = str(method_text).split()
    clean_tokens = [
        token for token in tokens
        if token.lower() not in excluded
    ]

    return " ".join(clean_tokens).strip()


def extract_final_sp_energy_from_text(out_text, source_label="output"):
    values = []

    for line in out_text.splitlines():
        if "FINAL SINGLE POINT ENERGY" in line:
            try:
                values.append(float(line.split()[-1]))
            except Exception:
                pass

    if not values:
        raise RuntimeError(
            f"Could not find FINAL SINGLE POINT ENERGY in {source_label}."
        )

    return float(values[-1])


def extract_symmetry_and_rot_constants_from_text(out_text, source_label="output"):
    symmetry_number = None
    rotational_constants_cm1 = None

    for line in out_text.splitlines():
        if "Symmetry Number" in line:
            match = re.search(
                r"Symmetry Number:\s*([0-9]+)",
                line,
                re.IGNORECASE
            )
            if match:
                symmetry_number = int(match.group(1))

        if "Rotational constants in cm-1" in line:
            nums = re.findall(
                r"[-+]?\d*\.\d+(?:[Ee][-+]?\d+)?|[-+]?\d+(?:[Ee][-+]?\d+)?",
                line
            )

            if len(nums) >= 3:
                rotational_constants_cm1 = np.array(
                    [float(nums[-3]), float(nums[-2]), float(nums[-1])],
                    dtype=float
                )

    if symmetry_number is None:
        symmetry_number = 1

    if rotational_constants_cm1 is None:
        raise RuntimeError(
            f"Could not find rotational constants in {source_label}."
        )

    return symmetry_number, rotational_constants_cm1


def extract_orca_frequency_blocks_from_text(out_text, source_label="output"):
    blocks = []
    current = None

    for raw_line in out_text.splitlines():
        line = raw_line.strip()

        if line.startswith("VIBRATIONAL FREQUENCIES"):
            if current is not None and len(current["freqs"]) > 0:
                blocks.append(current)

            current = {
                "title": line,
                "freqs": []
            }
            continue

        if current is not None:
            if line == "" or line.startswith("-") or line.startswith("Scaling factor"):
                continue

            parts = line.replace(":", " ").split()

            if len(parts) >= 3 and parts[0].isdigit() and "cm**-1" in parts:
                try:
                    current["freqs"].append(float(parts[1]))
                except Exception:
                    pass
                continue

            if len(current["freqs"]) > 0 and (
                line.startswith("NORMAL MODES")
                or line.startswith("IR SPECTRUM")
                or line.startswith("THERMOCHEMISTRY")
                or line.startswith("ORCA TERMINATED")
            ):
                blocks.append(current)
                current = None

    if current is not None and len(current["freqs"]) > 0:
        blocks.append(current)

    for block in blocks:
        freqs = np.array(block["freqs"], dtype=float)
        block["freqs"] = freqs
        block["imag"] = freqs[freqs < 0.0]
        block["zero"] = freqs[np.isclose(freqs, 0.0, atol=1.0e-6)]
        block["real"] = freqs[freqs > 0.0]

    if not blocks:
        raise RuntimeError(f"No vibrational frequency block found in {source_label}.")

    return blocks


def select_reference_frequency_block_from_text(out_text, source_label="reference output"):
    blocks = extract_orca_frequency_blocks_from_text(out_text, source_label)

    print("\nDetected reference/reactant frequency blocks:")
    for idx, block in enumerate(blocks, start=1):
        print(
            f"  Block {idx}: {block['title']} | "
            f"imag={len(block['imag'])}, "
            f"zero={len(block['zero'])}, "
            f"real={len(block['real'])}"
        )

    no_imag = [block for block in blocks if len(block["imag"]) == 0]

    if no_imag:
        return max(no_imag, key=lambda block: len(block["real"]))

    print("WARNING: Reference has imaginary frequencies.")
    print("Using the frequency block with the fewest imaginary modes.")

    return sorted(
        blocks,
        key=lambda block: (len(block["imag"]), -len(block["real"]))
    )[0]


def select_mecp_pes2_frequency_block_from_text(out_text, source_label="MECP output"):
    blocks = extract_orca_frequency_blocks_from_text(out_text, source_label)

    print(f"\nDetected MECP frequency blocks in {source_label}:")
    for idx, block in enumerate(blocks, start=1):
        print(
            f"  Block {idx}: {block['title']} | "
            f"imag={len(block['imag'])}, "
            f"zero={len(block['zero'])}, "
            f"real={len(block['real'])}"
        )

    pes2_blocks = [
        block for block in blocks
        if "PES2" in block["title"].upper()
    ]

    if not pes2_blocks:
        raise RuntimeError(f"No PES2 frequency block found in {source_label}.")

    pes2_no_imag = [
        block for block in pes2_blocks
        if len(block["imag"]) == 0
    ]

    if pes2_no_imag:
        return max(pes2_no_imag, key=lambda block: len(block["real"]))

    print("WARNING: PES2 block has imaginary frequencies.")
    print("Keeping the PES2 block with the fewest imaginary modes as diagnostic.")

    return sorted(
        pes2_blocks,
        key=lambda block: (len(block["imag"]), -len(block["real"]))
    )[0]


def zpe_from_freqs_cm1(freqs_cm1):
    freqs = np.asarray(freqs_cm1, dtype=float)
    freqs = freqs[np.isfinite(freqs) & (freqs > 0.0)]
    return 0.5 * float(np.sum(freqs))


def build_mecp_freq_input(geom_text, xyz_mult, mecp_mult):
    mecp_block = f"""%mecp Mult {int(mecp_mult)}
end
"""

    pre_xyz = make_dft_pre_xyz(
        "freq",
        job_specific_blocks=mecp_block
    )

    return build_orca_input_from_pre_xyz(
        pre_xyz,
        charge,
        xyz_mult,
        geom_text
    )


def remote_find_file(remote_dir, filename):
    path = posixpath.join(remote_dir, filename)

    if remote_file_exists(sftp, path):
        return path

    raise RuntimeError(f"Required remote file was not found:\n{path}")


def download_remote_file_if_present(remote_path, local_path):
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        sftp.get(remote_path, local_path)
        return True
    except Exception:
        return False


# ============================================================
# Reference/reactant data from Step 1
# ============================================================

section("REFERENCE DATA FROM STEP 1")

ref_data = minima_data[Reference_multiplicity]

Reference_geometry = ref_data["geometry"]
Reference_source = globals().get(
    "Reference_source",
    f"Reference minimum from Step 1, multiplicity {Reference_multiplicity}"
)

if "Electronic_Eh" in ref_data:
    Ele_REF_hartree = float(ref_data["Electronic_Eh"])
elif "Ele_hartree" in ref_data:
    Ele_REF_hartree = float(ref_data["Ele_hartree"])
else:
    raise RuntimeError("Could not find reference electronic energy in minima_data.")

if "ZPE_Eh" in ref_data:
    ZPE_REF_thermo_hartree = float(ref_data["ZPE_Eh"])
elif "ZPE_hartree" in ref_data:
    ZPE_REF_thermo_hartree = float(ref_data["ZPE_hartree"])
else:
    raise RuntimeError("Could not find reference ZPE in minima_data.")

E_ref_hartree = Ele_REF_hartree
ZPE_REF_hartree = ZPE_REF_thermo_hartree
ZPE_ref_hartree = ZPE_REF_thermo_hartree

remote_ref_out = ref_data["out_path"]
remote_ref_xyz = ref_data["xyz_path"]

ref_out_text = read_remote_text_file(remote_ref_out)

Reference_symmetry_number, Reference_rot_constants_cm1 = (
    extract_symmetry_and_rot_constants_from_text(
        ref_out_text,
        source_label=remote_ref_out
    )
)

ref_block = select_reference_frequency_block_from_text(
    ref_out_text,
    source_label=remote_ref_out
)

freq_reactant_cm1_all = ref_block["freqs"]
freq_reactant_real_cm1 = ref_block["real"]
freq_reactant_imag_cm1 = ref_block["imag"]
freq_reactant_zero_cm1 = ref_block["zero"]

ZPE_REF_from_freq_cm1 = zpe_from_freqs_cm1(freq_reactant_real_cm1)
ZPE_REF_from_freq_hartree = ZPE_REF_from_freq_cm1 / hartree_to_cm
ZPE_REF_freq_hartree = ZPE_REF_from_freq_hartree

print("\nReference/reactant data reused from Step 1:")
print(f"  Reference source                 = {Reference_source}")
print(f"  Reference multiplicity           = {Reference_multiplicity}")
print(f"  Reference out                    = {remote_ref_out}")
print(f"  Reference xyz                    = {remote_ref_xyz}")
print(f"  Ele_REF_hartree                  = {Ele_REF_hartree:.12f}")
print(f"  ZPE_REF_thermo_hartree           = {ZPE_REF_thermo_hartree:.12f}")
print(f"  ZPE_REF_from_freq_cm1            = {ZPE_REF_from_freq_cm1:.6f}")
print(f"  ZPE_REF_from_freq_hartree        = {ZPE_REF_from_freq_hartree:.12f}")
print(f"  Reference real frequencies       = {len(freq_reactant_real_cm1)}")
print(f"  Reference imaginary frequencies  = {len(freq_reactant_imag_cm1)}")
print(f"  Reference symmetry number        = {Reference_symmetry_number}")
print(f"  Reference rot constants cm^-1    = {Reference_rot_constants_cm1}")

if len(freq_reactant_imag_cm1) > 0:
    print("\nWARNING: Reference/reactant has imaginary frequencies:")
    print(freq_reactant_imag_cm1)

# ============================================================
# Remote/local frequency directories
# ============================================================

section("MECP FREQUENCY DIRECTORY PREPARATION")

remote_hess_dir = posixpath.join(remote_base, "Frequencies")
remote_mkdir_p(sftp, remote_hess_dir)

local_hess_dir = os.path.join(local_base, "Frequencies")
os.makedirs(local_hess_dir, exist_ok=True)

print(f"Remote Hessian/frequency directory : {remote_hess_dir}")
print(f"Local Hessian/frequency directory  : {local_hess_dir}")
print("Reference frequency data are reused from Step 1.")
print("Only the two MECP SurfCrossNumFreq jobs are generated here.")

freq_mecp_main_other_label = f"{jobname}_Freq_MECP_main_other"
freq_mecp_other_main_label = f"{jobname}_Freq_MECP_other_main"

# ============================================================
# Build and submit two MECP SurfCrossNumFreq jobs simultaneously
# ============================================================

section("SUBMITTING MECP SURFCROSSNUMFREQ JOBS")

mecp_main_other_inp = build_mecp_freq_input(
    MECP_geometry,
    xyz_mult=mult_main,
    mecp_mult=mult_other
)

mecp_other_main_inp = build_mecp_freq_input(
    MECP_geometry,
    xyz_mult=mult_other,
    mecp_mult=mult_main
)

freq_jobs = [
    {
        "jobname": freq_mecp_main_other_label,
        "inp_text": mecp_main_other_inp,
        "job_label": f"MECP SurfCrossNumFreq: xyz mult {mult_main}, partner mult {mult_other}",
        "xyz_mult": mult_main,
        "mecp_mult": mult_other
    },
    {
        "jobname": freq_mecp_other_main_label,
        "inp_text": mecp_other_main_inp,
        "job_label": f"MECP SurfCrossNumFreq: xyz mult {mult_other}, partner mult {mult_main}",
        "xyz_mult": mult_other,
        "mecp_mult": mult_main
    }
]

for job in freq_jobs:
    local_inp = os.path.join(local_hess_dir, f"{job['jobname']}.inp")
    write_local_text(local_inp, job["inp_text"])

    subsection(f"Generated ORCA input | {job['jobname']}.inp")
    print(job["inp_text"])

submitted_freq_jobs = []

for job in freq_jobs:
    rec = submit_orca_job_no_monitor(
        remote_hess_dir,
        job["jobname"],
        job["inp_text"],
        job["job_label"],
        attempt=1
    )

    rec["xyz_mult"] = job["xyz_mult"]
    rec["mecp_mult"] = job["mecp_mult"]

    rec["repair_policy"] = "freq_restricted"
    rec["locked"] = {
        "method": clean_method_for_frequency(method),
        "basis": basis,
        "charge": charge,
        "mult": job["xyz_mult"],
        "mecp_mult": job["mecp_mult"],
        "geom": MECP_geometry,
    }

    submitted_freq_jobs.append(rec)

print("\nBoth MECP SurfCrossNumFreq jobs have been submitted.")
print("The two calculations will now be monitored simultaneously.\n")

submitted_freq_jobs = monitor_submitted_orca_jobs(
    remote_hess_dir,
    submitted_freq_jobs,
    allow_interactive_repair=True
)

for rec in submitted_freq_jobs:
    if rec["status"] != "OK":
        raise SystemExit(f"{rec['jobname']} did not complete successfully.")

# ============================================================
# MECP Hessian paths and diagnostic PES2 frequencies
# ============================================================

section("MECP HESSIAN AND FREQUENCY EXTRACTION")

remote_mecp_main_other_out = remote_find_file(
    remote_hess_dir,
    f"{freq_mecp_main_other_label}.out"
)

remote_mecp_other_main_out = remote_find_file(
    remote_hess_dir,
    f"{freq_mecp_other_main_label}.out"
)

remote_mecp_main_other_hess = remote_find_file(
    remote_hess_dir,
    f"{freq_mecp_main_other_label}.hess"
)

remote_mecp_other_main_hess = remote_find_file(
    remote_hess_dir,
    f"{freq_mecp_other_main_label}.hess"
)

mecp_main_other_out_text = read_remote_text_file(remote_mecp_main_other_out)
mecp_other_main_out_text = read_remote_text_file(remote_mecp_other_main_out)

MECP_symmetry_number, MECP_rot_constants_cm1 = (
    extract_symmetry_and_rot_constants_from_text(
        mecp_main_other_out_text,
        source_label=remote_mecp_main_other_out
    )
)

mecp_pes2_block = select_mecp_pes2_frequency_block_from_text(
    mecp_main_other_out_text,
    source_label=remote_mecp_main_other_out
)

freq_MECP_PES2_cm1_all = mecp_pes2_block["freqs"]
freq_MECP_PES2_real_cm1 = mecp_pes2_block["real"]
freq_MECP_PES2_imag_cm1 = mecp_pes2_block["imag"]
freq_MECP_PES2_zero_cm1 = mecp_pes2_block["zero"]

freq_MECP_PES2_real_cm1_diagnostic = freq_MECP_PES2_real_cm1
freq_MECP_PES2_imag_cm1_diagnostic = freq_MECP_PES2_imag_cm1
freq_MECP_PES2_zero_cm1_diagnostic = freq_MECP_PES2_zero_cm1

local_mecp_main_other_out = os.path.join(
    local_hess_dir,
    f"{freq_mecp_main_other_label}.out"
)

local_mecp_other_main_out = os.path.join(
    local_hess_dir,
    f"{freq_mecp_other_main_label}.out"
)

local_mecp_main_other_hess = os.path.join(
    local_hess_dir,
    f"{freq_mecp_main_other_label}.hess"
)

local_mecp_other_main_hess = os.path.join(
    local_hess_dir,
    f"{freq_mecp_other_main_label}.hess"
)

write_local_text(local_mecp_main_other_out, mecp_main_other_out_text)
write_local_text(local_mecp_other_main_out, mecp_other_main_out_text)

download_remote_file_if_present(remote_mecp_main_other_hess, local_mecp_main_other_hess)
download_remote_file_if_present(remote_mecp_other_main_hess, local_mecp_other_main_hess)

# Compatibility names
remote_freq_mecp_out = remote_mecp_main_other_out
remote_freq_mecp_hess = remote_mecp_main_other_hess

local_freq_mecp_out = os.path.join(local_hess_dir, "Freq_MECP.out")
local_freq_mecp_hess = os.path.join(local_hess_dir, "Freq_MECP.hess")

write_local_text(local_freq_mecp_out, mecp_main_other_out_text)
download_remote_file_if_present(remote_mecp_main_other_hess, local_freq_mecp_hess)

print("\nMECP Hessian jobs completed:")
print(f"  main/other output              = {remote_mecp_main_other_out}")
print(f"  main/other hessian             = {remote_mecp_main_other_hess}")
print(f"  other/main output              = {remote_mecp_other_main_out}")
print(f"  other/main hessian             = {remote_mecp_other_main_hess}")
print(f"  MECP symmetry number           = {MECP_symmetry_number}")
print(f"  MECP rot constants cm^-1       = {MECP_rot_constants_cm1}")

print("\nDiagnostic ORCA PES2 frequencies from SurfCrossNumFreq:")
print(f"  block                          = {mecp_pes2_block['title']}")
print(f"  real                           = {len(freq_MECP_PES2_real_cm1_diagnostic)}")
print(f"  imag                           = {len(freq_MECP_PES2_imag_cm1_diagnostic)}")
print("  NOTE: NAST freX must come from Step 6 projected effective Hessian.")

# ============================================================
# Electronic barrier bookkeeping
# ============================================================

section("ELECTRONIC BARRIER BOOKKEEPING")

remote_mecp_optimization_out = None

if "remote_mecp" in globals():
    candidate = posixpath.join(remote_mecp, f"{jobname}.out")
    if remote_file_exists(sftp, candidate):
        remote_mecp_optimization_out = candidate

if remote_mecp_optimization_out is not None:
    mecp_opt_text = read_remote_text_file(remote_mecp_optimization_out)
    Ele_MECP_hartree = extract_final_sp_energy_from_text(
        mecp_opt_text,
        source_label=remote_mecp_optimization_out
    )
    MECP_energy_source = remote_mecp_optimization_out
else:
    Ele_MECP_hartree = extract_final_sp_energy_from_text(
        mecp_main_other_out_text,
        source_label=remote_mecp_main_other_out
    )
    MECP_energy_source = remote_mecp_main_other_out

E_MECP_electronic_hartree = Ele_MECP_hartree - Ele_REF_hartree
VaG_MECP_electronic_cm1 = E_MECP_electronic_hartree * hartree_to_cm

# Provisional only. Step 6/7 will replace this with effective-Hessian ZPE correction.
VaG_MECP_cm1 = VaG_MECP_electronic_cm1
VaG_MECP_kJmol = VaG_MECP_cm1 * 0.01196266
E_MECP = VaG_MECP_cm1
E_MECP_cm1 = VaG_MECP_cm1

print(f"Reference electronic energy       = {Ele_REF_hartree:.12f} Eh")
print(f"MECP electronic energy source     = {MECP_energy_source}")
print(f"MECP electronic energy            = {Ele_MECP_hartree:.12f} Eh")
print(f"Electronic MECP barrier           = {VaG_MECP_electronic_cm1:.6f} cm^-1")
print(f"Provisional active barrier        = {VaG_MECP_cm1:.6f} cm^-1")
print("NOTE: Step 6/7 should replace this with effective-Hessian ZPE correction.")

# ============================================================
# NAST-style variable preparation
# ============================================================

section("NAST-STYLE VARIABLE PREPARATION")

sigma_R = float(Reference_symmetry_number)
sigma_X = float(MECP_symmetry_number)

MECP_hess_main_other_path = local_mecp_main_other_hess
MECP_hess_other_main_path = local_mecp_other_main_hess

MECP_hess_main_other_remote = remote_mecp_main_other_hess
MECP_hess_other_main_remote = remote_mecp_other_main_hess

MECP_out_main_other_path = local_mecp_main_other_out
MECP_out_other_main_path = local_mecp_other_main_out

MECP_out_main_other_remote = remote_mecp_main_other_out
MECP_out_other_main_remote = remote_mecp_other_main_out

MECP_mult_main = mult_main
MECP_mult_other = mult_other

freR_cm1 = freq_reactant_real_cm1
inertR_rot_constants_cm1 = Reference_rot_constants_cm1
inertX_rot_constants_cm1 = MECP_rot_constants_cm1

print("Prepared NAST-style variables:")
print("  freR_cm1")
print("  inertR_rot_constants_cm1")
print("  inertX_rot_constants_cm1")
print("  sigma_R")
print("  sigma_X")
print("  MECP_hess_main_other_path")
print("  MECP_hess_other_main_path")

# ============================================================
# Save Step 5 metadata
# ============================================================

section("SAVING STEP 5 OUTPUTS")

rot_info_file = os.path.join(
    local_hess_dir,
    f"{jobname}_rotational_constants_and_symmetry.txt"
)

rot_lines = []

rot_lines.append("Rotational constants and symmetry numbers")
rot_lines.append("Reference data reused from Step 1 minimum Opt Freq output")
rot_lines.append("MECP data from SurfCrossNumFreq output")
rot_lines.append("")

rot_lines.append("[REFERENCE]")
rot_lines.append(f"output_file_remote = {remote_ref_out}")
rot_lines.append(f"xyz_file_remote = {remote_ref_xyz}")
rot_lines.append(f"multiplicity = {Reference_multiplicity}")
rot_lines.append(f"symmetry_number = {Reference_symmetry_number}")
rot_lines.append(
    "rotational_constants_cm1 = "
    + " ".join(f"{x:.12f}" for x in Reference_rot_constants_cm1)
)
rot_lines.append("")

rot_lines.append("[MECP_MAIN_OTHER]")
rot_lines.append(f"output_file_remote = {remote_mecp_main_other_out}")
rot_lines.append(f"hessian_file_remote = {remote_mecp_main_other_hess}")
rot_lines.append(f"output_file_local = {local_mecp_main_other_out}")
rot_lines.append(f"hessian_file_local = {local_mecp_main_other_hess}")
rot_lines.append(f"xyz_multiplicity = {mult_main}")
rot_lines.append(f"mecp_multiplicity = {mult_other}")
rot_lines.append(f"symmetry_number = {MECP_symmetry_number}")
rot_lines.append(
    "rotational_constants_cm1 = "
    + " ".join(f"{x:.12f}" for x in MECP_rot_constants_cm1)
)
rot_lines.append("")

rot_lines.append("[MECP_OTHER_MAIN]")
rot_lines.append(f"output_file_remote = {remote_mecp_other_main_out}")
rot_lines.append(f"hessian_file_remote = {remote_mecp_other_main_hess}")
rot_lines.append(f"output_file_local = {local_mecp_other_main_out}")
rot_lines.append(f"hessian_file_local = {local_mecp_other_main_hess}")
rot_lines.append(f"xyz_multiplicity = {mult_other}")
rot_lines.append(f"mecp_multiplicity = {mult_main}")
rot_lines.append("")

rot_lines.append("[BARRIER]")
rot_lines.append(f"Ele_REF_hartree = {Ele_REF_hartree:.12f}")
rot_lines.append(f"Ele_MECP_hartree = {Ele_MECP_hartree:.12f}")
rot_lines.append(f"MECP_energy_source = {MECP_energy_source}")
rot_lines.append(f"VaG_MECP_electronic_cm1 = {VaG_MECP_electronic_cm1:.12f}")
rot_lines.append(f"VaG_MECP_cm1_provisional = {VaG_MECP_cm1:.12f}")
rot_lines.append("")

rot_lines.append("[REFERENCE_FREQUENCIES]")
rot_lines.append(f"real_count = {len(freq_reactant_real_cm1)}")
rot_lines.append(f"imag_count = {len(freq_reactant_imag_cm1)}")
rot_lines.append(f"zero_count = {len(freq_reactant_zero_cm1)}")
rot_lines.append(f"ZPE_REF_from_freq_cm1 = {ZPE_REF_from_freq_cm1:.12f}")
rot_lines.append(f"ZPE_REF_from_freq_hartree = {ZPE_REF_from_freq_hartree:.12f}")
rot_lines.append("")

rot_lines.append("[MECP_PES2_DIAGNOSTIC_FREQUENCIES]")
rot_lines.append(f"block = {mecp_pes2_block['title']}")
rot_lines.append(f"real_count = {len(freq_MECP_PES2_real_cm1_diagnostic)}")
rot_lines.append(f"imag_count = {len(freq_MECP_PES2_imag_cm1_diagnostic)}")
rot_lines.append(f"zero_count = {len(freq_MECP_PES2_zero_cm1_diagnostic)}")
rot_lines.append("NOTE = Diagnostic only. Step 6 projected effective Hessian defines final freX.")

write_local_text(rot_info_file, "\n".join(rot_lines) + "\n")

remote_rot_info_file = posixpath.join(
    remote_hess_dir,
    posixpath.basename(rot_info_file)
)

sftp.put(rot_info_file, remote_rot_info_file)

# Download reference output locally for convenience
try:
    sftp.get(
        remote_ref_out,
        os.path.join(local_hess_dir, f"{jobname}_Reference_minimum.out")
    )
except Exception:
    pass

# ============================================================
# Export variables
# ============================================================

globals().update({
    "hartree_to_cm": hartree_to_cm,
    "workflow_mode": workflow_mode,
    "remote_hess_dir": remote_hess_dir,
    "local_hess_dir": local_hess_dir,
    "Reference_geometry": Reference_geometry,
    "Reference_source": Reference_source,
    "Ele_REF_hartree": Ele_REF_hartree,
    "E_ref_hartree": E_ref_hartree,
    "ZPE_REF_thermo_hartree": ZPE_REF_thermo_hartree,
    "ZPE_REF_hartree": ZPE_REF_hartree,
    "ZPE_ref_hartree": ZPE_ref_hartree,
    "ZPE_REF_from_freq_cm1": ZPE_REF_from_freq_cm1,
    "ZPE_REF_from_freq_hartree": ZPE_REF_from_freq_hartree,
    "ZPE_REF_freq_hartree": ZPE_REF_freq_hartree,
    "freq_reactant_cm1_all": freq_reactant_cm1_all,
    "freq_reactant_real_cm1": freq_reactant_real_cm1,
    "freq_reactant_imag_cm1": freq_reactant_imag_cm1,
    "freq_reactant_zero_cm1": freq_reactant_zero_cm1,
    "freR_cm1": freR_cm1,
    "Reference_symmetry_number": Reference_symmetry_number,
    "Reference_rot_constants_cm1": Reference_rot_constants_cm1,
    "MECP_symmetry_number": MECP_symmetry_number,
    "MECP_rot_constants_cm1": MECP_rot_constants_cm1,
    "sigma_R": sigma_R,
    "sigma_X": sigma_X,
    "freq_MECP_PES2_cm1_all": freq_MECP_PES2_cm1_all,
    "freq_MECP_PES2_real_cm1": freq_MECP_PES2_real_cm1,
    "freq_MECP_PES2_imag_cm1": freq_MECP_PES2_imag_cm1,
    "freq_MECP_PES2_zero_cm1": freq_MECP_PES2_zero_cm1,
    "freq_MECP_PES2_real_cm1_diagnostic": freq_MECP_PES2_real_cm1_diagnostic,
    "freq_MECP_PES2_imag_cm1_diagnostic": freq_MECP_PES2_imag_cm1_diagnostic,
    "freq_MECP_PES2_zero_cm1_diagnostic": freq_MECP_PES2_zero_cm1_diagnostic,
    "Ele_MECP_hartree": Ele_MECP_hartree,
    "E_MECP_electronic_hartree": E_MECP_electronic_hartree,
    "VaG_MECP_electronic_cm1": VaG_MECP_electronic_cm1,
    "VaG_MECP_cm1": VaG_MECP_cm1,
    "VaG_MECP_kJmol": VaG_MECP_kJmol,
    "E_MECP": E_MECP,
    "E_MECP_cm1": E_MECP_cm1,
    "MECP_energy_source": MECP_energy_source,
    "MECP_hess_main_other_path": MECP_hess_main_other_path,
    "MECP_hess_other_main_path": MECP_hess_other_main_path,
    "MECP_hess_main_other_remote": MECP_hess_main_other_remote,
    "MECP_hess_other_main_remote": MECP_hess_other_main_remote,
    "MECP_out_main_other_path": MECP_out_main_other_path,
    "MECP_out_other_main_path": MECP_out_other_main_path,
    "MECP_out_main_other_remote": MECP_out_main_other_remote,
    "MECP_out_other_main_remote": MECP_out_other_main_remote,
    "MECP_mult_main": MECP_mult_main,
    "MECP_mult_other": MECP_mult_other,
    "inertR_rot_constants_cm1": inertR_rot_constants_cm1,
    "inertX_rot_constants_cm1": inertX_rot_constants_cm1,
    "rot_info_file": rot_info_file,
    "remote_rot_info_file": remote_rot_info_file
})

# ============================================================
# Final summary
# ============================================================

section("STEP 5 SUMMARY")

print(f"Reference multiplicity              : {Reference_multiplicity}")
print(f"Reference real frequencies          : {len(freq_reactant_real_cm1)}")
print(f"Reference symmetry number           : {Reference_symmetry_number}")
print(f"MECP symmetry number                : {MECP_symmetry_number}")
print(f"Electronic MECP barrier             : {VaG_MECP_electronic_cm1:.6f} cm^-1")
print(f"Provisional active barrier          : {VaG_MECP_cm1:.6f} cm^-1")
print(f"Local Hessian directory             : {local_hess_dir}")
print(f"Remote Hessian directory            : {remote_hess_dir}")
print(f"Rotational/symmetry metadata local  : {rot_info_file}")
print(f"Rotational/symmetry metadata remote : {remote_rot_info_file}")

print("\nAvailable Step 5 variables:")
print("  freR_cm1")
print("  freq_reactant_real_cm1")
print("  freq_reactant_imag_cm1")
print("  Reference_rot_constants_cm1")
print("  MECP_rot_constants_cm1")
print("  Reference_symmetry_number")
print("  MECP_symmetry_number")
print("  Ele_REF_hartree")
print("  ZPE_REF_thermo_hartree")
print("  ZPE_REF_from_freq_cm1")
print("  ZPE_REF_freq_hartree")
print("  Ele_MECP_hartree")
print("  VaG_MECP_electronic_cm1")
print("  VaG_MECP_cm1                  # provisional electronic barrier only")
print("  freq_MECP_PES2_real_cm1_diagnostic")
print("  MECP_hess_main_other_path")
print("  MECP_hess_other_main_path")
print("  MECP_hess_main_other_remote")
print("  MECP_hess_other_main_remote")
print("  sigma_R")
print("  sigma_X")
print("  rot_info_file")

print("\nSTEP 5 COMPLETED SUCCESSFULLY.\n")

#%% STEP 6. CLUSTER NAST-STYLE EFFECTIVE-HESSIAN RC REDUCED MASS

import os
import re
import posixpath
import numpy as np
from scipy.linalg import orth

print(r'''
====================================================================
 STEP 6 | CLUSTER VERSION
 NAST-style effective Hessian, reaction coordinate, and reduced mass
====================================================================

This step uses the two MECP SurfCrossNumFreq Hessians generated in
Step 5 and the two MECP gradients generated in Step 3.

No new ORCA calculation is performed here.

The main tasks are:

  1. Read the two ORCA .hess files.
  2. Construct mass-weighted Hessians.
  3. Build the NAST-style effective Hessian.
  4. Remove translation, rotation, and the reaction-coordinate direction.
  5. Extract the effective-Hessian transverse MECP frequencies.
  6. Define the NAST reaction-coordinate reduced mass.

The final downstream quantities are:

  DELTAF_PARALLEL_EH_PER_BOHR
  GRADMEAN_EH_PER_BOHR
  reduced_mass_amu
  freq_MECP_effhess_real_cm1
  reaction_direction_hessian_cart
  reaction_direction_hessian_mw
''')

# ============================================================
# Required variables from Steps 1–5
# ============================================================

required_vars_step6 = [
    "jobname",
    "MECP_geometry",
    "g_LS",
    "g_HS",
    "mult_LS",
    "mult_HS",
    "spin_name_LS",
    "spin_name_HS",
    "MECP_hess_main_other_path",
    "MECP_hess_other_main_path",
    "local_base",
    "remote_base"
]

for var in required_vars_step6:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Steps 1–5 first."
        )

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = "MECP_ONLY"

# ============================================================
# Helpers
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def subsection(title):
    print("\n" + "-" * 72)
    print(title)
    print("-" * 72 + "\n")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def normalize_vector(v, label):
    v = np.asarray(v, dtype=float).reshape(-1)
    nrm = float(np.linalg.norm(v))

    if not np.isfinite(nrm) or nrm <= 0.0:
        raise RuntimeError(f"Invalid vector norm for {label}.")

    return v / nrm


def atomic_contribution_from_vector(k_cart_like, symbols):
    atom_contrib = []

    for a, sym in enumerate(symbols):
        idx = 3 * a
        amp = float(np.linalg.norm(k_cart_like[idx:idx + 3]))
        atom_contrib.append((a + 1, sym, amp))

    atom_contrib.sort(key=lambda x: x[2], reverse=True)
    return atom_contrib


# ============================================================
# Atomic masses
# ============================================================

atomic_masses = {
    "H": 1.008,
    "D": 2.014,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998403163,
    "Na": 22.98976928,
    "Mg": 24.305,
    "Al": 26.9815385,
    "Si": 28.085,
    "P": 30.973761998,
    "S": 32.06,
    "Cl": 35.45,
    "K": 39.0983,
    "Ca": 40.078,
    "Sc": 44.955908,
    "Ti": 47.867,
    "V": 50.9415,
    "Cr": 51.9961,
    "Mn": 54.938044,
    "Fe": 55.845,
    "Co": 58.933194,
    "Ni": 58.6934,
    "Cu": 63.546,
    "Zn": 65.38,
    "Ga": 69.723,
    "Ge": 72.630,
    "As": 74.921595,
    "Se": 78.971,
    "Br": 79.904,
    "Rb": 85.4678,
    "Sr": 87.62,
    "Y": 88.90584,
    "Zr": 91.224,
    "Nb": 92.90637,
    "Mo": 95.95,
    "Tc": 98.0,
    "Ru": 101.07,
    "Rh": 102.90550,
    "Pd": 106.42,
    "Ag": 107.8682,
    "Cd": 112.414,
    "In": 114.818,
    "Sn": 118.710,
    "Sb": 121.760,
    "Te": 127.60,
    "I": 126.90447,
    "Cs": 132.90545196,
    "Ba": 137.327,
    "La": 138.90547,
    "Hf": 178.49,
    "Ta": 180.94788,
    "W": 183.84,
    "Re": 186.207,
    "Os": 190.23,
    "Ir": 192.217,
    "Pt": 195.084,
    "Au": 196.966569,
    "Hg": 200.592
}

# ============================================================
# Geometry parsing
# ============================================================

section("MECP GEOMETRY AND MASS PREPARATION")

symbols_mecp = []
coords_mecp = []

for line in MECP_geometry.splitlines():
    parts = line.split()

    if len(parts) >= 4:
        sym = parts[0]
        sym = re.sub(r"[^A-Za-z]", "", sym)
        sym = sym[0].upper() + sym[1:].lower()

        symbols_mecp.append(sym)
        coords_mecp.append([
            float(parts[1]),
            float(parts[2]),
            float(parts[3])
        ])

if not symbols_mecp:
    raise RuntimeError("Could not parse MECP_geometry.")

missing_masses = sorted(set([s for s in symbols_mecp if s not in atomic_masses]))

if missing_masses:
    raise RuntimeError(
        "Missing atomic masses for: "
        + ", ".join(missing_masses)
    )

coords_mecp = np.asarray(coords_mecp, dtype=float)
masses = np.asarray([atomic_masses[s] for s in symbols_mecp], dtype=float)

natoms = len(symbols_mecp)
num_cart = 3 * natoms

mass_vector = np.repeat(masses, 3)
sqrt_mass_vector = np.sqrt(mass_vector)

print(f"Number of atoms              : {natoms}")
print(f"Cartesian dimension          : {num_cart}")
print(f"Total mass                   : {np.sum(masses):.8f} amu")

# ============================================================
# Gradient and force definitions
# ============================================================

section("GRADIENT AND FORCE DEFINITIONS")

g_LS = np.asarray(g_LS, dtype=float).reshape(-1)
g_HS = np.asarray(g_HS, dtype=float).reshape(-1)

if g_LS.size != num_cart:
    raise RuntimeError(
        f"g_LS length {g_LS.size} does not match 3N = {num_cart}."
    )

if g_HS.size != num_cart:
    raise RuntimeError(
        f"g_HS length {g_HS.size} does not match 3N = {num_cart}."
    )

gLS_norm = float(np.linalg.norm(g_LS))
gHS_norm = float(np.linalg.norm(g_HS))
grad_dot = float(np.dot(g_LS, g_HS))

if gLS_norm <= 0.0 or gHS_norm <= 0.0:
    raise RuntimeError("Invalid MECP gradient norm.")

# Backward-compatible Landau-Zener crossing direction.
delta_g_cart = g_HS - g_LS
sum_g_cart = g_HS + g_LS

# NAST/effhess sign convention also retained.
deltaG_cart_NAST = g_LS - g_HS
deltaG_LS_minus_HS = deltaG_cart_NAST
deltaG_HS_minus_LS = delta_g_cart

DeltaG_norm_Eh_per_Bohr = float(np.linalg.norm(delta_g_cart))

if not np.isfinite(DeltaG_norm_Eh_per_Bohr) or DeltaG_norm_Eh_per_Bohr <= 0.0:
    raise RuntimeError("Invalid gradient-difference norm.")

n_cart = delta_g_cart / DeltaG_norm_Eh_per_Bohr
n_cart_NAST = deltaG_cart_NAST / DeltaG_norm_Eh_per_Bohr

reaction_direction_cart = n_cart.copy()
MECP_seam_normal_cart = n_cart.copy()

DELTAF_PARALLEL_EH_PER_BOHR = DeltaG_norm_Eh_per_Bohr
DeltaF_parallel = DELTAF_PARALLEL_EH_PER_BOHR

F_LS_signed = float(np.dot(g_LS, n_cart))
F_HS_signed = float(np.dot(g_HS, n_cart))

F_LS_parallel_Eh_per_Bohr = abs(F_LS_signed)
F_HS_parallel_Eh_per_Bohr = abs(F_HS_signed)

GRADMEAN_EH_PER_BOHR = float(
    np.sqrt(F_LS_parallel_Eh_per_Bohr * F_HS_parallel_Eh_per_Bohr)
)

gradmean_Eh_per_Bohr = GRADMEAN_EH_PER_BOHR

mu_inv_gradient_direction = float(np.sum((n_cart ** 2) / mass_vector))
reduced_mass_gradient_direction_amu = 1.0 / mu_inv_gradient_direction

if grad_dot < 0.0:
    intersection_type = "peaked"
else:
    intersection_type = "sloped"

print(f"Low-spin surface                 : {spin_name_LS}, multiplicity {mult_LS}")
print(f"High-spin surface                : {spin_name_HS}, multiplicity {mult_HS}")
print(f"||g_LS||                         : {gLS_norm:.12e} Eh/Bohr")
print(f"||g_HS||                         : {gHS_norm:.12e} Eh/Bohr")
print(f"g_LS · g_HS                      : {grad_dot:.12e}")
print(f"Intersection type                : {intersection_type}")
print(f"DELTAF_PARALLEL_EH_PER_BOHR      : {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
print(f"F_LS_parallel                    : {F_LS_parallel_Eh_per_Bohr:.12e}")
print(f"F_HS_parallel                    : {F_HS_parallel_Eh_per_Bohr:.12e}")
print(f"GRADMEAN_EH_PER_BOHR             : {GRADMEAN_EH_PER_BOHR:.12e}")
print(f"Cartesian Δg reduced mass         : {reduced_mass_gradient_direction_amu:.8f} amu")

# ============================================================
# ORCA Hessian parser
# ============================================================

def read_orca_hessian(hess_path, expected_dim):
    if not os.path.isfile(hess_path):
        raise RuntimeError(f"Hessian file not found:\n{hess_path}")

    try:
        H_plain = np.loadtxt(hess_path)

        if (
            H_plain.ndim == 2
            and H_plain.shape == (expected_dim, expected_dim)
        ):
            return 0.5 * (H_plain + H_plain.T)

    except Exception:
        pass

    with open(hess_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    start = None

    for i, line in enumerate(lines):
        if line.strip().lower().startswith("$hessian"):
            start = i
            break

    if start is None:
        raise RuntimeError(f"Could not find $hessian block in:\n{hess_path}")

    dim = None
    dim_line_index = None

    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()

        if not stripped:
            continue

        if stripped.startswith("$"):
            break

        nums = re.findall(r"[-+]?\d+", stripped)

        if nums:
            dim = int(nums[0])
            dim_line_index = i
            break

    if dim != expected_dim:
        raise RuntimeError(
            f"Hessian dimension {dim}, expected {expected_dim}, file:\n{hess_path}"
        )

    H = np.zeros((dim, dim), dtype=float)
    i = dim_line_index + 1

    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        if line.startswith("$"):
            break

        header_ints = re.findall(r"[-+]?\d+", line)

        if not header_ints:
            i += 1
            continue

        col_indices = [int(x) for x in header_ints]
        i += 1
        rows_read = 0

        while i < len(lines) and rows_read < dim:
            row_line = lines[i].strip()

            if not row_line:
                i += 1
                continue

            if row_line.startswith("$"):
                break

            row_line = row_line.replace("D", "E").replace("d", "E")

            nums = re.findall(
                r"[-+]?\d*\.\d+(?:[Ee][-+]?\d+)?|[-+]?\d+(?:[Ee][-+]?\d+)?",
                row_line
            )

            if len(nums) >= 2:
                row_idx = int(float(nums[0]))
                vals = [float(x) for x in nums[1:]]

                for c, val in zip(col_indices, vals):
                    if 0 <= row_idx < dim and 0 <= c < dim:
                        H[row_idx, c] = val

                rows_read += 1

            i += 1

    H = 0.5 * (H + H.T)

    if not np.any(np.abs(H) > 0.0):
        raise RuntimeError(f"Parsed Hessian is all zeros:\n{hess_path}")

    return H


# ============================================================
# Load and mass-weight MECP Hessians
# ============================================================

section("READING AND MASS-WEIGHTING MECP HESSIANS")

print(f"MECP Hessian main/other : {MECP_hess_main_other_path}")
print(f"MECP Hessian other/main : {MECP_hess_other_main_path}")

H_mecp_main_other_cart = read_orca_hessian(
    MECP_hess_main_other_path,
    expected_dim=num_cart
)

H_mecp_other_main_cart = read_orca_hessian(
    MECP_hess_other_main_path,
    expected_dim=num_cart
)

mass_weight_matrix = 1.0 / np.sqrt(np.outer(mass_vector, mass_vector))

H_mecp_main_other_mw = H_mecp_main_other_cart * mass_weight_matrix
H_mecp_other_main_mw = H_mecp_other_main_cart * mass_weight_matrix

print("Both Hessians were parsed successfully.")
print(f"Hessian dimension : {H_mecp_main_other_cart.shape}")

# ============================================================
# Effective Hessian
# ============================================================

section("NAST EFFECTIVE-HESSIAN CONSTRUCTION")

H_eff_mw_plus = (
    gHS_norm * H_mecp_main_other_mw
    + gLS_norm * H_mecp_other_main_mw
) / (gLS_norm + gHS_norm)

if abs(gHS_norm - gLS_norm) > 1.0e-14:
    H_eff_mw_minus = (
        gHS_norm * H_mecp_main_other_mw
        - gLS_norm * H_mecp_other_main_mw
    ) / (gHS_norm - gLS_norm)
else:
    H_eff_mw_minus = H_eff_mw_plus.copy()

if intersection_type == "peaked":
    H_eff_mw_selected = H_eff_mw_plus.copy()
    effective_hessian_choice = "plus/peaked"
else:
    H_eff_mw_selected = H_eff_mw_minus.copy()
    effective_hessian_choice = "minus/sloped"

H_eff_mw_average = 0.5 * (
    H_mecp_main_other_mw + H_mecp_other_main_mw
)

H_eff_mw_gradweighted = H_eff_mw_selected.copy()

print(f"Intersection type          : {intersection_type}")
print(f"Effective Hessian selected : {effective_hessian_choice}")

# ============================================================
# Translation/rotation projector
# ============================================================

section("TRANSLATION/ROTATION PROJECTOR")

R_cm = np.sum(coords_mecp * masses[:, None], axis=0) / np.sum(masses)
coords_centered = coords_mecp - R_cm

Z_raw = np.zeros((num_cart, 6), dtype=float)

# Translations
for k in range(3):
    for a in range(natoms):
        Z_raw[3 * a + k, k] = np.sqrt(masses[a])

# Rotations
for a in range(natoms):
    x, y, z = coords_centered[a]
    m_sqrt = np.sqrt(masses[a])
    idx = 3 * a

    Z_raw[idx,     3] = 0.0
    Z_raw[idx + 1, 3] = m_sqrt * z
    Z_raw[idx + 2, 3] = -m_sqrt * y

    Z_raw[idx,     4] = -m_sqrt * z
    Z_raw[idx + 1, 4] = 0.0
    Z_raw[idx + 2, 4] = m_sqrt * x

    Z_raw[idx,     5] = m_sqrt * y
    Z_raw[idx + 1, 5] = -m_sqrt * x
    Z_raw[idx + 2, 5] = 0.0

Z = orth(Z_raw)
P_TR = Z @ Z.T
P_RT = np.eye(num_cart) - P_TR

print(f"Rigid-body subspace dimension : {Z.shape[1]}")
print(f"||P_TR^2 - P_TR||             : {np.linalg.norm(P_TR @ P_TR - P_TR):.3e}")
print(f"||P_RT^2 - P_RT||             : {np.linalg.norm(P_RT @ P_RT - P_RT):.3e}")

# ============================================================
# RC mass helpers
# ============================================================

def rc_mass_from_vector(k_mw):
    k_mw = normalize_vector(k_mw, "mass-weighted RC vector")

    k_cart_mass_unweighted = k_mw / sqrt_mass_vector
    denom = float(np.dot(k_cart_mass_unweighted, k_cart_mass_unweighted))

    if denom <= 0.0 or not np.isfinite(denom):
        return np.nan, k_cart_mass_unweighted

    return 1.0 / denom, k_cart_mass_unweighted


def nast_rc_from_seed(seed_cart, label):
    seed_cart = np.asarray(seed_cart, dtype=float).reshape(-1)

    seed_mw_div = seed_cart / sqrt_mass_vector
    seed_mw_mul = seed_cart * sqrt_mass_vector

    candidates = []

    for convention, seed_mw_raw in [
        ("seed/sqrt(m)", seed_mw_div),
        ("seed*sqrt(m)", seed_mw_mul)
    ]:
        seed_mw = P_RT @ seed_mw_raw
        seed_mw = normalize_vector(seed_mw, label + " " + convention)

        P_RC_seed = np.outer(seed_mw, seed_mw)
        P_TOTAL = P_TR + P_RC_seed

        H_proj = (
            (np.eye(num_cart) - P_TOTAL)
            @ H_eff_mw_selected
            @ (np.eye(num_cart) - P_TOTAL)
        )
        H_proj = 0.5 * (H_proj + H_proj.T)

        eigvals, eigvecs = np.linalg.eigh(H_proj)

        idx_abs = np.argsort(np.abs(eigvals))
        eigvals_abs_sorted = eigvals[idx_abs]
        eigvecs_abs_sorted = eigvecs[:, idx_abs]

        scores = []

        for j in range(min(12, num_cart)):
            v = normalize_vector(
                eigvecs_abs_sorted[:, j],
                "projected zero-mode candidate"
            )

            rt_ov = float(np.linalg.norm(Z.T @ v) ** 2)
            rc_ov = float(abs(np.dot(seed_mw, v)) ** 2)
            eig_abs = float(abs(eigvals_abs_sorted[j]))

            scores.append((j, rc_ov, rt_ov, eig_abs))

        scores_sorted = sorted(
            scores,
            key=lambda x: (x[1] - x[2], -x[3]),
            reverse=True
        )

        best_local_idx = scores_sorted[0][0]

        k_zero = normalize_vector(
            eigvecs_abs_sorted[:, best_local_idx],
            "selected projected RC zero eigenvector"
        )

        if np.dot(k_zero, seed_mw) < 0.0:
            k_zero = -k_zero

        mu_zero, k_zero_cart_like = rc_mass_from_vector(k_zero)

        H_RC = P_RC_seed @ H_eff_mw_selected @ P_RC_seed
        H_RC = 0.5 * (H_RC + H_RC.T)

        eigvals_rc, eigvecs_rc = np.linalg.eigh(H_RC)
        idx_rc = int(np.argmax(np.abs(eigvecs_rc.T @ seed_mw)))

        k_rc_direct = normalize_vector(
            eigvecs_rc[:, idx_rc],
            "direct Eq. 7.3 RC vector"
        )

        if np.dot(k_rc_direct, seed_mw) < 0.0:
            k_rc_direct = -k_rc_direct

        mu_direct, k_direct_cart_like = rc_mass_from_vector(k_rc_direct)

        candidates.append({
            "label": label,
            "convention": convention,
            "seed_mw": seed_mw,
            "P_RC": P_RC_seed,
            "P_TOTAL": P_TOTAL,
            "H_proj": H_proj,
            "eigvals_proj": eigvals,
            "eigvecs_proj": eigvecs,
            "zero_mode_scores": scores,
            "best_zero_local_idx": int(best_local_idx),
            "best_zero_eigval": float(eigvals_abs_sorted[best_local_idx]),
            "k_zero": k_zero,
            "k_zero_cart_like": k_zero_cart_like,
            "mu_zero_amu": float(mu_zero),
            "H_RC": H_RC,
            "eigvals_rc": eigvals_rc,
            "eigvecs_rc": eigvecs_rc,
            "k_rc_direct": k_rc_direct,
            "k_rc_direct_cart_like": k_direct_cart_like,
            "mu_direct_amu": float(mu_direct),
            "rt_overlap_zero": float(np.linalg.norm(Z.T @ k_zero) ** 2),
            "rc_overlap_zero": float(abs(np.dot(seed_mw, k_zero)) ** 2)
        })

    return candidates


# ============================================================
# RC candidates and final selection
# ============================================================

section("REACTION-COORDINATE CANDIDATES")

g_LS_unit = g_LS / gLS_norm
g_HS_unit = g_HS / gHS_norm

rc_candidates = []

rc_candidates += nast_rc_from_seed(delta_g_cart, "Δg = g_HS - g_LS")
rc_candidates += nast_rc_from_seed(sum_g_cart, "g_sum = g_HS + g_LS")
rc_candidates += nast_rc_from_seed(g_HS_unit - g_LS_unit, "unit Δg")
rc_candidates += nast_rc_from_seed(g_HS_unit + g_LS_unit, "unit g_sum")

selected_rc = None

for cand in rc_candidates:
    if (
        cand["label"].startswith("Δg")
        and cand["convention"] == "seed/sqrt(m)"
    ):
        selected_rc = cand
        break

if selected_rc is None:
    raise RuntimeError(
        "Could not select required RC candidate: Δg = g_HS - g_LS with seed/sqrt(m)."
    )

reduced_mass_amu = float(selected_rc["mu_direct_amu"])

reaction_direction_hessian_mw = selected_rc["seed_mw"]
reaction_direction_hessian_cart = selected_rc["k_rc_direct_cart_like"]
reaction_direction_hessian_cart = normalize_vector(
    reaction_direction_hessian_cart,
    "reaction_direction_hessian_cart"
)

P_RC = selected_rc["P_RC"]
P_TOTAL = selected_rc["P_TOTAL"]
H_proj_selected = selected_rc["H_proj"]
H_RC_selected = selected_rc["H_RC"]

reduced_mass_effhess_zero_selected_amu = float(selected_rc["mu_zero_amu"])
reduced_mass_effhess_direct_selected_amu = float(selected_rc["mu_direct_amu"])

print("Final automatic RC selection:")
print("  Selection rule 1 : use Δg = g_HS - g_LS")
print("  Selection rule 2 : use seed/sqrt(m) mass-weighted convention")
print("  Selection rule 3 : use Eq. 7.3 direct RC mass downstream")
print("")
print(f"  Selected label              : {selected_rc['label']}")
print(f"  Selected convention         : {selected_rc['convention']}")
print(f"  Eq. 7.2 zero-mode mass       : {reduced_mass_effhess_zero_selected_amu:.8f} amu")
print(f"  Eq. 7.3 direct mass          : {reduced_mass_effhess_direct_selected_amu:.8f} amu")
print(f"  FINAL reduced_mass_amu       : {reduced_mass_amu:.8f} amu")

# ============================================================
# Projected transverse effective-Hessian frequencies
# ============================================================

section("PROJECTED EFFECTIVE-HESSIAN FREQUENCIES")

P_REMOVE = P_TOTAL
P_KEEP = np.eye(num_cart) - P_REMOVE

H_eff_projected_transverse = (
    P_KEEP @ H_eff_mw_selected @ P_KEEP
)
H_eff_projected_transverse = 0.5 * (
    H_eff_projected_transverse + H_eff_projected_transverse.T
)

eigvals_eff, eigvecs_eff = np.linalg.eigh(H_eff_projected_transverse)

idx_sort = np.argsort(eigvals_eff)
eigvals_eff = eigvals_eff[idx_sort]
eigvecs_eff = eigvecs_eff[:, idx_sort]

rt_overlap_eff = np.asarray([
    np.linalg.norm(Z.T @ eigvecs_eff[:, j]) ** 2
    for j in range(num_cart)
])

rc_overlap_eff = np.asarray([
    abs(np.dot(reaction_direction_hessian_mw, eigvecs_eff[:, j])) ** 2
    for j in range(num_cart)
])

zero_like_mask = (
    (rt_overlap_eff > 1.0e-6)
    | (rc_overlap_eff > 1.0e-6)
)

conversion_factor = 5140.48
freq_eff_cm1 = np.zeros(num_cart)

for j in range(num_cart):
    if zero_like_mask[j]:
        freq_eff_cm1[j] = 0.0
    else:
        val = eigvals_eff[j]
        freq_eff_cm1[j] = np.sign(val) * np.sqrt(abs(val)) * conversion_factor

freq_MECP_effhess_real_cm1 = freq_eff_cm1[freq_eff_cm1 > 1.0e-8]
freq_MECP_effhess_imag_cm1 = freq_eff_cm1[freq_eff_cm1 < -1.0e-8]
freq_MECP_effhess_zero_cm1 = freq_eff_cm1[
    np.isclose(freq_eff_cm1, 0.0, atol=1.0e-8)
]

freq_MECP_real_cm1 = freq_MECP_effhess_real_cm1
freq_MECP_imag_cm1 = freq_MECP_effhess_imag_cm1
freq_MECP_zero_cm1 = freq_MECP_effhess_zero_cm1

print(f"Zero-like modes removed          : {len(freq_MECP_effhess_zero_cm1)}")
print(f"Real transverse frequencies      : {len(freq_MECP_effhess_real_cm1)}")
print(f"Imaginary transverse frequencies : {len(freq_MECP_effhess_imag_cm1)}")

if len(freq_MECP_effhess_imag_cm1) > 0:
    print("\nImaginary effective-Hessian frequencies:")
    print(freq_MECP_effhess_imag_cm1)

# ============================================================
# Store Hessian arrays
# ============================================================

hessian_data_MECP = np.zeros((num_cart, num_cart, 2), dtype=float)
hessian_data_MECP[:, :, 0] = H_mecp_main_other_cart
hessian_data_MECP[:, :, 1] = H_mecp_other_main_cart

mw_hessian_data_MECP = np.zeros((num_cart, num_cart, 2), dtype=float)
mw_hessian_data_MECP[:, :, 0] = H_mecp_main_other_mw
mw_hessian_data_MECP[:, :, 1] = H_mecp_other_main_mw

# ============================================================
# Diagnostics
# ============================================================

section("RC CANDIDATE DIAGNOSTICS")

for i, cand in enumerate(rc_candidates, start=1):
    print(f"{i:2d}. {cand['label']:25s} | {cand['convention']:12s}")
    print(f"    Eq. 7.2 zero-mode μ       = {cand['mu_zero_amu']:.8f} amu")
    print(f"    Eq. 7.3 direct μ          = {cand['mu_direct_amu']:.8f} amu")
    print(f"    zero eigenvalue           = {cand['best_zero_eigval']:.12e}")
    print(f"    RC overlap                = {cand['rc_overlap_zero']:.8f}")
    print(f"    RT overlap                = {cand['rt_overlap_zero']:.8e}")

section("SELECTED RC ATOMIC CONTRIBUTIONS")

atom_contrib = atomic_contribution_from_vector(
    reaction_direction_hessian_cart,
    symbols_mecp
)

for atom_idx, sym, amp in atom_contrib[:15]:
    print(f"Atom {atom_idx:3d} ({sym:2s})   amplitude = {amp:.8f}")

# ============================================================
# Save Step 6 outputs
# ============================================================

section("SAVING STEP 6 OUTPUTS")

local_step6 = os.path.join(local_base, "Effective_Hessian_RC")
os.makedirs(local_step6, exist_ok=True)

remote_step6 = posixpath.join(remote_base, "Effective_Hessian_RC")

if "remote_mkdir_p" in globals() and "sftp" in globals():
    try:
        remote_mkdir_p(sftp, remote_step6)
    except Exception:
        pass

summary_file_step6 = os.path.join(
    local_step6,
    f"{jobname}_Step6_effective_hessian_RC_summary.txt"
)

freq_file_step6 = os.path.join(
    local_step6,
    f"{jobname}_Step6_effective_hessian_frequencies.txt"
)

rc_vector_file_step6 = os.path.join(
    local_step6,
    f"{jobname}_Step6_reaction_coordinate_vectors.txt"
)

summary_lines = []

summary_lines.append("Step 6 NAST-style effective-Hessian RC reduced mass")
summary_lines.append(f"Run mode = {RUN_MODE}")
summary_lines.append(f"Workflow mode = {workflow_mode}")
summary_lines.append("")
summary_lines.append(f"Low-spin multiplicity = {mult_LS} ({spin_name_LS})")
summary_lines.append(f"High-spin multiplicity = {mult_HS} ({spin_name_HS})")
summary_lines.append("")
summary_lines.append("[GRADIENTS]")
summary_lines.append(f"gLS_norm = {gLS_norm:.12e} Eh/Bohr")
summary_lines.append(f"gHS_norm = {gHS_norm:.12e} Eh/Bohr")
summary_lines.append(f"g_LS_dot_g_HS = {grad_dot:.12e}")
summary_lines.append(f"intersection_type = {intersection_type}")
summary_lines.append(f"DELTAF_PARALLEL_EH_PER_BOHR = {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
summary_lines.append(f"F_LS_parallel_Eh_per_Bohr = {F_LS_parallel_Eh_per_Bohr:.12e}")
summary_lines.append(f"F_HS_parallel_Eh_per_Bohr = {F_HS_parallel_Eh_per_Bohr:.12e}")
summary_lines.append(f"GRADMEAN_EH_PER_BOHR = {GRADMEAN_EH_PER_BOHR:.12e}")
summary_lines.append("")
summary_lines.append("[HESSIANS]")
summary_lines.append(f"MECP_hess_main_other_path = {MECP_hess_main_other_path}")
summary_lines.append(f"MECP_hess_other_main_path = {MECP_hess_other_main_path}")
summary_lines.append(f"effective_hessian_choice = {effective_hessian_choice}")
summary_lines.append("")
summary_lines.append("[REDUCED_MASSES]")
summary_lines.append(f"reduced_mass_gradient_direction_amu = {reduced_mass_gradient_direction_amu:.12f}")
summary_lines.append(f"reduced_mass_effhess_zero_selected_amu = {reduced_mass_effhess_zero_selected_amu:.12f}")
summary_lines.append(f"reduced_mass_effhess_direct_selected_amu = {reduced_mass_effhess_direct_selected_amu:.12f}")
summary_lines.append(f"FINAL reduced_mass_amu = {reduced_mass_amu:.12f}")
summary_lines.append("")
summary_lines.append("[SELECTED_RC]")
summary_lines.append(f"selected_label = {selected_rc['label']}")
summary_lines.append(f"selected_convention = {selected_rc['convention']}")
summary_lines.append(f"selected_zero_eigenvalue = {selected_rc['best_zero_eigval']:.12e}")
summary_lines.append(f"selected_rc_overlap = {selected_rc['rc_overlap_zero']:.12e}")
summary_lines.append(f"selected_rt_overlap = {selected_rc['rt_overlap_zero']:.12e}")
summary_lines.append("")
summary_lines.append("[FREQUENCIES]")
summary_lines.append(f"freq_MECP_effhess_real_count = {len(freq_MECP_effhess_real_cm1)}")
summary_lines.append(f"freq_MECP_effhess_imag_count = {len(freq_MECP_effhess_imag_cm1)}")
summary_lines.append(f"freq_MECP_effhess_zero_count = {len(freq_MECP_effhess_zero_cm1)}")
summary_lines.append("")
summary_lines.append("[RC_CANDIDATES]")

for i, cand in enumerate(rc_candidates, start=1):
    summary_lines.append(
        f"{i:02d} | {cand['label']} | {cand['convention']} | "
        f"mu_zero = {cand['mu_zero_amu']:.12f} | "
        f"mu_direct = {cand['mu_direct_amu']:.12f} | "
        f"eig_zero = {cand['best_zero_eigval']:.12e} | "
        f"rc_overlap = {cand['rc_overlap_zero']:.12e} | "
        f"rt_overlap = {cand['rt_overlap_zero']:.12e}"
    )

summary_lines.append("")
summary_lines.append("[SELECTED_RC_ATOMIC_CONTRIBUTIONS]")

for atom_idx, sym, amp in atom_contrib:
    summary_lines.append(f"{atom_idx:5d} {sym:3s} {amp:.12e}")

write_local_text(summary_file_step6, "\n".join(summary_lines) + "\n")

freq_lines = []
freq_lines.append("Step 6 effective-Hessian projected MECP frequencies")
freq_lines.append("")
freq_lines.append("[REAL_CM-1]")
for value in freq_MECP_effhess_real_cm1:
    freq_lines.append(f"{value:.12f}")

freq_lines.append("")
freq_lines.append("[IMAG_CM-1]")
for value in freq_MECP_effhess_imag_cm1:
    freq_lines.append(f"{value:.12f}")

freq_lines.append("")
freq_lines.append("[ZERO_CM-1]")
for value in freq_MECP_effhess_zero_cm1:
    freq_lines.append(f"{value:.12f}")

write_local_text(freq_file_step6, "\n".join(freq_lines) + "\n")

vector_lines = []
vector_lines.append("Step 6 reaction-coordinate vectors")
vector_lines.append("")
vector_lines.append("# reaction_direction_hessian_cart")
for value in reaction_direction_hessian_cart:
    vector_lines.append(f"{value: .12e}")

vector_lines.append("")
vector_lines.append("# reaction_direction_hessian_mw")
for value in reaction_direction_hessian_mw:
    vector_lines.append(f"{value: .12e}")

vector_lines.append("")
vector_lines.append("# n_cart = g_HS - g_LS normalized")
for value in n_cart:
    vector_lines.append(f"{value: .12e}")

vector_lines.append("")
vector_lines.append("# n_cart_NAST = g_LS - g_HS normalized")
for value in n_cart_NAST:
    vector_lines.append(f"{value: .12e}")

write_local_text(rc_vector_file_step6, "\n".join(vector_lines) + "\n")

remote_summary_file_step6 = None
remote_freq_file_step6 = None
remote_rc_vector_file_step6 = None

if "sftp" in globals():
    try:
        remote_summary_file_step6 = posixpath.join(
            remote_step6,
            posixpath.basename(summary_file_step6)
        )
        remote_freq_file_step6 = posixpath.join(
            remote_step6,
            posixpath.basename(freq_file_step6)
        )
        remote_rc_vector_file_step6 = posixpath.join(
            remote_step6,
            posixpath.basename(rc_vector_file_step6)
        )

        sftp.put(summary_file_step6, remote_summary_file_step6)
        sftp.put(freq_file_step6, remote_freq_file_step6)
        sftp.put(rc_vector_file_step6, remote_rc_vector_file_step6)

    except Exception:
        remote_summary_file_step6 = None
        remote_freq_file_step6 = None
        remote_rc_vector_file_step6 = None

print(f"Local Step 6 summary       : {summary_file_step6}")
print(f"Local Step 6 frequencies   : {freq_file_step6}")
print(f"Local Step 6 RC vectors    : {rc_vector_file_step6}")

if remote_summary_file_step6:
    print(f"Remote Step 6 summary      : {remote_summary_file_step6}")
    print(f"Remote Step 6 frequencies  : {remote_freq_file_step6}")
    print(f"Remote Step 6 RC vectors   : {remote_rc_vector_file_step6}")

# ============================================================
# Export variables for later steps
# ============================================================

globals().update({
    "RUN_MODE": RUN_MODE,
    "workflow_mode": workflow_mode,
    "symbols_mecp": symbols_mecp,
    "coords_mecp": coords_mecp,
    "masses": masses,
    "mass_vector": mass_vector,
    "sqrt_mass_vector": sqrt_mass_vector,
    "natoms": natoms,
    "num_cart": num_cart,
    "g_LS": g_LS,
    "g_HS": g_HS,
    "gLS_norm": gLS_norm,
    "gHS_norm": gHS_norm,
    "grad_dot": grad_dot,
    "delta_g_cart": delta_g_cart,
    "deltaG_cart_NAST": deltaG_cart_NAST,
    "deltaG_LS_minus_HS": deltaG_LS_minus_HS,
    "deltaG_HS_minus_LS": deltaG_HS_minus_LS,
    "n_cart": n_cart,
    "n_cart_NAST": n_cart_NAST,
    "reaction_direction_cart": reaction_direction_cart,
    "MECP_seam_normal_cart": MECP_seam_normal_cart,
    "DELTAF_PARALLEL_EH_PER_BOHR": DELTAF_PARALLEL_EH_PER_BOHR,
    "DeltaF_parallel": DeltaF_parallel,
    "F_LS_signed": F_LS_signed,
    "F_HS_signed": F_HS_signed,
    "F_LS_parallel_Eh_per_Bohr": F_LS_parallel_Eh_per_Bohr,
    "F_HS_parallel_Eh_per_Bohr": F_HS_parallel_Eh_per_Bohr,
    "GRADMEAN_EH_PER_BOHR": GRADMEAN_EH_PER_BOHR,
    "gradmean_Eh_per_Bohr": gradmean_Eh_per_Bohr,
    "reduced_mass_gradient_direction_amu": reduced_mass_gradient_direction_amu,
    "intersection_type": intersection_type,
    "H_mecp_main_other_cart": H_mecp_main_other_cart,
    "H_mecp_other_main_cart": H_mecp_other_main_cart,
    "H_mecp_main_other_mw": H_mecp_main_other_mw,
    "H_mecp_other_main_mw": H_mecp_other_main_mw,
    "H_eff_mw_plus": H_eff_mw_plus,
    "H_eff_mw_minus": H_eff_mw_minus,
    "H_eff_mw_selected": H_eff_mw_selected,
    "H_eff_mw_average": H_eff_mw_average,
    "H_eff_mw_gradweighted": H_eff_mw_gradweighted,
    "effective_hessian_choice": effective_hessian_choice,
    "Z": Z,
    "P_TR": P_TR,
    "P_RT": P_RT,
    "P_RC": P_RC,
    "P_TOTAL": P_TOTAL,
    "H_proj_selected": H_proj_selected,
    "H_RC_selected": H_RC_selected,
    "rc_candidates": rc_candidates,
    "selected_rc": selected_rc,
    "reaction_direction_hessian_cart": reaction_direction_hessian_cart,
    "reaction_direction_hessian_mw": reaction_direction_hessian_mw,
    "reduced_mass_amu": reduced_mass_amu,
    "reduced_mass_effhess_zero_selected_amu": reduced_mass_effhess_zero_selected_amu,
    "reduced_mass_effhess_direct_selected_amu": reduced_mass_effhess_direct_selected_amu,
    "H_eff_projected_transverse": H_eff_projected_transverse,
    "eigvals_eff": eigvals_eff,
    "eigvecs_eff": eigvecs_eff,
    "rt_overlap_eff": rt_overlap_eff,
    "rc_overlap_eff": rc_overlap_eff,
    "freq_eff_cm1": freq_eff_cm1,
    "freq_MECP_effhess_real_cm1": freq_MECP_effhess_real_cm1,
    "freq_MECP_effhess_imag_cm1": freq_MECP_effhess_imag_cm1,
    "freq_MECP_effhess_zero_cm1": freq_MECP_effhess_zero_cm1,
    "freq_MECP_real_cm1": freq_MECP_real_cm1,
    "freq_MECP_imag_cm1": freq_MECP_imag_cm1,
    "freq_MECP_zero_cm1": freq_MECP_zero_cm1,
    "hessian_data_MECP": hessian_data_MECP,
    "mw_hessian_data_MECP": mw_hessian_data_MECP,
    "local_step6": local_step6,
    "remote_step6": remote_step6,
    "summary_file_step6": summary_file_step6,
    "freq_file_step6": freq_file_step6,
    "rc_vector_file_step6": rc_vector_file_step6,
    "remote_summary_file_step6": remote_summary_file_step6,
    "remote_freq_file_step6": remote_freq_file_step6,
    "remote_rc_vector_file_step6": remote_rc_vector_file_step6
})

# ============================================================
# Final summary
# ============================================================

section("STEP 6 SUMMARY")

print(f"Intersection type                    : {intersection_type}")
print(f"Effective Hessian choice             : {effective_hessian_choice}")
print(f"DELTAF_PARALLEL_EH_PER_BOHR          : {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
print(f"GRADMEAN_EH_PER_BOHR                 : {GRADMEAN_EH_PER_BOHR:.12e}")
print(f"Cartesian Δg reduced mass             : {reduced_mass_gradient_direction_amu:.8f} amu")
print(f"Selected Eq. 7.2 zero-mode mass       : {reduced_mass_effhess_zero_selected_amu:.8f} amu")
print(f"Selected Eq. 7.3 direct mass          : {reduced_mass_effhess_direct_selected_amu:.8f} amu")
print(f"FINAL reduced_mass_amu                : {reduced_mass_amu:.8f} amu")
print(f"Effective-Hessian real frequencies    : {len(freq_MECP_effhess_real_cm1)}")
print(f"Effective-Hessian imaginary freqs     : {len(freq_MECP_effhess_imag_cm1)}")
print(f"Effective-Hessian zero-like modes      : {len(freq_MECP_effhess_zero_cm1)}")

print("\nImportant variables available for later steps:")
print("  DELTAF_PARALLEL_EH_PER_BOHR")
print("  GRADMEAN_EH_PER_BOHR")
print("  reduced_mass_amu")
print("  reduced_mass_gradient_direction_amu")
print("  reduced_mass_effhess_zero_selected_amu")
print("  reduced_mass_effhess_direct_selected_amu")
print("  reaction_direction_hessian_cart")
print("  reaction_direction_hessian_mw")
print("  H_eff_mw_selected")
print("  H_eff_mw_average")
print("  H_eff_mw_plus")
print("  H_eff_mw_minus")
print("  hessian_data_MECP")
print("  mw_hessian_data_MECP")
print("  P_TR, P_RT, P_RC, P_TOTAL, Z")
print("  H_proj_selected")
print("  H_RC_selected")
print("  freq_MECP_effhess_real_cm1")
print("  freq_MECP_effhess_imag_cm1")
print("  freq_MECP_effhess_zero_cm1")
print("  freq_MECP_real_cm1")
print("  freq_MECP_imag_cm1")
print("  freq_MECP_zero_cm1")
print("  rc_candidates")
print("  selected_rc")

print("\nSTEP 6 COMPLETED SUCCESSFULLY.\n")


#%% STEP 7. CLUSTER NAST-STYLE TOTAL-ENERGY AND VELOCITY GRID

import os
import posixpath
import numpy as np
import matplotlib.pyplot as plt

print(r'''
====================================================================
 STEP 7 | CLUSTER VERSION
 NAST-style total-energy and reaction-coordinate velocity grid
====================================================================

This step uses the effective-Hessian MECP frequencies and reduced mass
from Step 6 to construct the common 1 cm^-1 energy grid used by the
probability and rate calculations.

No new ORCA calculation is performed here.

The active downstream MECP barrier is defined as

  VaG_MECP_cm1 =
      VaG_MECP_electronic_cm1
    + ZPE_effhess(MECP)
    - ZPE(reference)

where ZPE_effhess(MECP) is computed from the projected effective-Hessian
transverse frequencies generated in Step 6.

The common total-energy grid is

  E_bins_cm1 = 0, 1, 2, ..., maxn_py  cm^-1

and the reaction-coordinate excess energy is

  epsilon_rc_cm1 = E_bins_cm1 - VaG_MECP_cm1.

The velocity is evaluated exactly as in Step 9:

  v = sqrt(2 epsilon_rc / mu)     for epsilon_rc > 0
  v = 0                           for epsilon_rc <= 0

The same E_bins_cm1 and velocity arrays are exported for Step 8 so that
effective, intermediate, and MS-specific probabilities are evaluated on
the identical NAST grid used for the rate calculations.
''')

# ============================================================
# Required variables from Steps 1–6
# ============================================================

required_vars_step7 = [
    "jobname",
    "VaG_MECP_electronic_cm1",
    "reduced_mass_amu",
    "DELTAF_PARALLEL_EH_PER_BOHR",
    "GRADMEAN_EH_PER_BOHR",
    "freq_MECP_effhess_real_cm1",
    "local_base",
    "remote_base"
]

for var in required_vars_step7:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Steps 1–6 first."
        )

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = "MECP_ONLY"

# ============================================================
# Helpers
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def ask_float(prompt, default=None, minimum=None):
    while True:
        ans = input(prompt).strip()

        if ans == "" and default is not None:
            val = float(default)
        else:
            try:
                val = float(ans)
            except ValueError:
                print("  Please enter a valid numerical value.")
                continue

        if minimum is not None and val < minimum:
            print(f"  Please enter a value greater than or equal to {minimum}.")
            continue

        return val


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ============================================================
# Reference and MECP ZPE preparation
# ============================================================

section("REFERENCE AND MECP ZPE PREPARATION")

hartree_to_cm = float(globals().get("hartree_to_cm", 219474.6313705))

if "ZPE_REF_from_freq_cm1" not in globals():
    if "ZPE_REF_thermo_hartree" in globals():
        ZPE_REF_from_freq_cm1 = (
            float(ZPE_REF_thermo_hartree) * hartree_to_cm
        )
    elif "ZPE_REF_hartree" in globals():
        ZPE_REF_from_freq_cm1 = (
            float(ZPE_REF_hartree) * hartree_to_cm
        )
    else:
        raise RuntimeError(
            "Reference ZPE is missing. Need ZPE_REF_from_freq_cm1, "
            "ZPE_REF_thermo_hartree, or ZPE_REF_hartree from Step 5."
        )

freq_MECP_effhess_real_cm1 = np.asarray(
    freq_MECP_effhess_real_cm1,
    dtype=float
).reshape(-1)

freq_MECP_effhess_real_cm1 = freq_MECP_effhess_real_cm1[
    np.isfinite(freq_MECP_effhess_real_cm1)
    & (freq_MECP_effhess_real_cm1 > 0.0)
]

if freq_MECP_effhess_real_cm1.size == 0:
    raise RuntimeError(
        "freq_MECP_effhess_real_cm1 is empty. "
        "Step 6 did not produce usable MECP effective-Hessian frequencies."
    )

ZPE_MECP_effhess_cm1 = (
    0.5 * float(np.sum(freq_MECP_effhess_real_cm1))
)

Delta_ZPE_MECP_minus_REF_cm1 = (
    ZPE_MECP_effhess_cm1
    - float(ZPE_REF_from_freq_cm1)
)

VaG_MECP_ZPE_corrected_cm1 = (
    float(VaG_MECP_electronic_cm1)
    + Delta_ZPE_MECP_minus_REF_cm1
)

VaG_MECP_cm1 = float(VaG_MECP_ZPE_corrected_cm1)
VaG_MECP_kJmol = VaG_MECP_cm1 * 0.01196266

VaG_MECP_barrier_label = (
    "ZPE-corrected MECP barrier = electronic barrier "
    "+ ZPE_effhess(MECP) - ZPE(reference)"
)

E_MECP = VaG_MECP_cm1
E_MECP_cm1 = VaG_MECP_cm1

print("Barrier summary relative to reference minimum:")
print(f"  Electronic MECP barrier          = {VaG_MECP_electronic_cm1:.6f} cm^-1")
print(f"  Reference ZPE                    = {ZPE_REF_from_freq_cm1:.6f} cm^-1")
print(f"  MECP effective-Hessian ZPE       = {ZPE_MECP_effhess_cm1:.6f} cm^-1")
print(f"  Delta ZPE, MECP - REF            = {Delta_ZPE_MECP_minus_REF_cm1:.6f} cm^-1")
print(f"  Active ZPE-corrected barrier     = {VaG_MECP_cm1:.6f} cm^-1")
print(f"  Active ZPE-corrected barrier     = {VaG_MECP_kJmol:.6f} kJ/mol")

# ============================================================
# Common NAST total-energy grid
# ============================================================

section("COMMON NAST TOTAL-ENERGY GRID")

print(
    "The entered value is the maximum reaction-coordinate energy "
    "above the ZPE-corrected MECP barrier."
)
print(
    "Step 7 converts this value into the common 1 cm^-1 total-energy "
    "grid used by Steps 8 and 9.\n"
)

epsilon_rc_max_cm1 = ask_float(
    "Maximum reaction-coordinate energy above MECP, cm^-1, e.g. 5000: ",
    minimum=1.0e-12
)

Estep = 1.0

E_max_requested_cm1 = (
    float(VaG_MECP_cm1) + float(epsilon_rc_max_cm1)
)

maxn_py = int(np.ceil(E_max_requested_cm1 / Estep))

E_bins_cm1 = (
    np.arange(maxn_py + 1, dtype=float) * Estep
)

if E_bins_cm1.size == 0:
    raise RuntimeError("The common NAST energy grid is empty.")

binX = int(np.ceil(E_MECP / Estep))

E_LZ_cm1 = E_bins_cm1.copy()

epsilon_rc_cm1 = E_bins_cm1 - E_MECP
epsilon_rc_positive_cm1 = np.maximum(epsilon_rc_cm1, 0.0)

E_above_MECP_cm1 = epsilon_rc_cm1.copy()
dE_cm1 = epsilon_rc_cm1.copy()

nE = int(E_bins_cm1.size)

E_min_cm1 = float(E_bins_cm1[0])
E_max_cm1 = float(E_bins_cm1[-1])
E_max_nast_cm1 = E_max_cm1

print(f"Energy spacing                      = {Estep:.6f} cm^-1")
print(f"Number of total-energy bins         = {nE}")
print(f"MECP energy                         = {E_MECP:.6f} cm^-1")
print(f"MECP bin index                      = {binX}")
print(f"Requested maximum energy            = {E_max_requested_cm1:.6f} cm^-1")
print(f"Actual maximum NAST bin energy      = {E_max_cm1:.6f} cm^-1")
print(f"Total-energy grid                   = {E_min_cm1:.6f} to {E_max_cm1:.6f} cm^-1")
print(f"Requested energy above MECP         = 0.000000 to {epsilon_rc_max_cm1:.6f} cm^-1")

# ============================================================
# Velocity grid, identical to Step 9
# ============================================================

section("MICROCANONICAL VELOCITY GRID")

h_SI = 6.62607015e-34
c_SI = 2.99792458e8
amu_to_kg = 1.66053906660e-27
bohr_m = 5.29177210903e-11
bohr_to_m = bohr_m

mu_kg = float(reduced_mass_amu) * amu_to_kg

if not np.isfinite(mu_kg) or mu_kg <= 0.0:
    raise RuntimeError("Invalid reduced mass. Check Step 6.")

E_excess_J = (
    (E_bins_cm1 - E_MECP)
    * 100.0
    * h_SI
    * c_SI
)

v_m_s = np.zeros_like(E_bins_cm1, dtype=float)

mask_energy = E_excess_J > 0.0

v_m_s[mask_energy] = np.sqrt(
    2.0 * E_excess_J[mask_energy] / mu_kg
)

v_bohr_s = v_m_s / bohr_m

velocity_floor = 1.0e-12

v_bohr_s_safe = np.copy(v_bohr_s)
v_bohr_s_safe[v_bohr_s_safe <= velocity_floor] = 1.0e-300

epsilon_rc_J = E_excess_J.copy()

print("MECP velocity calculation completed.")
print(f"  reduced_mass_amu                  = {reduced_mass_amu:.8f} amu")
print(f"  mu_kg                             = {mu_kg:.12e} kg")
print(f"  DELTAF_PARALLEL_EH_PER_BOHR      = {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
print(f"  GRADMEAN_EH_PER_BOHR             = {GRADMEAN_EH_PER_BOHR:.12e}")
print(f"  Velocity range                    = {np.nanmin(v_m_s):.6e} to {np.nanmax(v_m_s):.6e} m/s")
print(f"  Velocity range                    = {np.nanmin(v_bohr_s):.6e} to {np.nanmax(v_bohr_s):.6e} Bohr/s")

# ============================================================
# Plot velocity
# ============================================================

section("PLOTTING VELOCITY GRID")

local_step7 = os.path.join(
    local_base,
    "RC_energy_velocity_grid"
)

os.makedirs(local_step7, exist_ok=True)

velocity_plot_file = os.path.join(
    local_step7,
    f"{jobname}_Step7_velocity_grid.png"
)

plt.figure(figsize=(7.5, 4.5))
plt.plot(E_bins_cm1, v_m_s, linewidth=2)
plt.axvline(
    E_MECP,
    linestyle="--",
    linewidth=1.5,
    label="ZPE-corrected MECP"
)
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("Velocity at MECP (m/s)")
plt.title("NAST-style Microcanonical Velocity at MECP")
plt.grid(False)
plt.legend()
plt.tight_layout()
plt.savefig(velocity_plot_file, dpi=300)
plt.show()

print(f"Velocity plot saved locally: {velocity_plot_file}")

# ============================================================
# Save Step 7 outputs
# ============================================================

section("SAVING STEP 7 OUTPUTS")

remote_step7 = posixpath.join(
    remote_base,
    "RC_energy_velocity_grid"
)

if "remote_mkdir_p" in globals() and "sftp" in globals():
    try:
        remote_mkdir_p(sftp, remote_step7)
    except Exception:
        pass

summary_file_step7 = os.path.join(
    local_step7,
    f"{jobname}_Step7_energy_velocity_summary.txt"
)

grid_file_step7 = os.path.join(
    local_step7,
    f"{jobname}_Step7_energy_velocity_grid.txt"
)

summary_lines = [
    "Step 7 common NAST total-energy and velocity grid",
    f"Run mode = {RUN_MODE}",
    f"Workflow mode = {workflow_mode}",
    "",
    "[BARRIER]",
    f"VaG_MECP_electronic_cm1 = {VaG_MECP_electronic_cm1:.12f}",
    f"ZPE_REF_from_freq_cm1 = {ZPE_REF_from_freq_cm1:.12f}",
    f"ZPE_MECP_effhess_cm1 = {ZPE_MECP_effhess_cm1:.12f}",
    f"Delta_ZPE_MECP_minus_REF_cm1 = {Delta_ZPE_MECP_minus_REF_cm1:.12f}",
    f"VaG_MECP_ZPE_corrected_cm1 = {VaG_MECP_ZPE_corrected_cm1:.12f}",
    f"VaG_MECP_cm1 = {VaG_MECP_cm1:.12f}",
    f"VaG_MECP_kJmol = {VaG_MECP_kJmol:.12f}",
    f"VaG_MECP_barrier_label = {VaG_MECP_barrier_label}",
    "",
    "[GRID]",
    f"Estep = {Estep:.12f}",
    f"maxn_py = {maxn_py}",
    f"binX = {binX}",
    f"nE = {nE}",
    f"epsilon_rc_max_cm1 = {epsilon_rc_max_cm1:.12f}",
    f"E_max_requested_cm1 = {E_max_requested_cm1:.12f}",
    f"E_min_cm1 = {E_min_cm1:.12f}",
    f"E_max_cm1 = {E_max_cm1:.12f}",
    f"E_max_nast_cm1 = {E_max_nast_cm1:.12f}",
    "",
    "[VELOCITY]",
    f"reduced_mass_amu = {reduced_mass_amu:.12f}",
    f"mu_kg = {mu_kg:.12e}",
    f"DELTAF_PARALLEL_EH_PER_BOHR = {DELTAF_PARALLEL_EH_PER_BOHR:.12e}",
    f"GRADMEAN_EH_PER_BOHR = {GRADMEAN_EH_PER_BOHR:.12e}",
    f"velocity_floor = {velocity_floor:.12e}",
    f"v_m_s_min = {np.nanmin(v_m_s):.12e}",
    f"v_m_s_max = {np.nanmax(v_m_s):.12e}",
    f"v_bohr_s_min = {np.nanmin(v_bohr_s):.12e}",
    f"v_bohr_s_max = {np.nanmax(v_bohr_s):.12e}",
    "",
    "[FILES]",
    f"velocity_plot_file = {velocity_plot_file}",
    f"grid_file_step7 = {grid_file_step7}",
]

write_local_text(
    summary_file_step7,
    "\n".join(summary_lines) + "\n"
)

grid_lines = [
    "E_bins_cm1  epsilon_rc_cm1  "
    "epsilon_rc_positive_cm1  E_excess_J  "
    "v_m_s  v_bohr_s  v_bohr_s_safe"
]

for E, eps, eps_pos, eps_J, vm, vb, vbs in zip(
    E_bins_cm1,
    epsilon_rc_cm1,
    epsilon_rc_positive_cm1,
    E_excess_J,
    v_m_s,
    v_bohr_s,
    v_bohr_s_safe
):
    grid_lines.append(
        f"{E:18.10f} "
        f"{eps:18.10f} "
        f"{eps_pos:18.10f} "
        f"{eps_J:18.10e} "
        f"{vm:18.10e} "
        f"{vb:18.10e} "
        f"{vbs:18.10e}"
    )

write_local_text(
    grid_file_step7,
    "\n".join(grid_lines) + "\n"
)

remote_summary_file_step7 = None
remote_grid_file_step7 = None
remote_velocity_plot_file = None

if "sftp" in globals():
    try:
        remote_summary_file_step7 = posixpath.join(
            remote_step7,
            posixpath.basename(summary_file_step7)
        )
        remote_grid_file_step7 = posixpath.join(
            remote_step7,
            posixpath.basename(grid_file_step7)
        )
        remote_velocity_plot_file = posixpath.join(
            remote_step7,
            posixpath.basename(velocity_plot_file)
        )

        sftp.put(summary_file_step7, remote_summary_file_step7)
        sftp.put(grid_file_step7, remote_grid_file_step7)
        sftp.put(velocity_plot_file, remote_velocity_plot_file)

    except Exception:
        remote_summary_file_step7 = None
        remote_grid_file_step7 = None
        remote_velocity_plot_file = None

print(f"Local Step 7 summary       : {summary_file_step7}")
print(f"Local Step 7 grid          : {grid_file_step7}")
print(f"Local Step 7 velocity plot : {velocity_plot_file}")

if remote_summary_file_step7:
    print(f"Remote Step 7 summary      : {remote_summary_file_step7}")
    print(f"Remote Step 7 grid         : {remote_grid_file_step7}")
    print(f"Remote Step 7 plot         : {remote_velocity_plot_file}")

# ============================================================
# Export variables for later steps
# ============================================================

globals().update({
    "RUN_MODE": RUN_MODE,
    "workflow_mode": workflow_mode,
    "hartree_to_cm": hartree_to_cm,
    "ZPE_REF_from_freq_cm1": ZPE_REF_from_freq_cm1,
    "ZPE_MECP_effhess_cm1": ZPE_MECP_effhess_cm1,
    "Delta_ZPE_MECP_minus_REF_cm1": Delta_ZPE_MECP_minus_REF_cm1,
    "VaG_MECP_ZPE_corrected_cm1": VaG_MECP_ZPE_corrected_cm1,
    "VaG_MECP_cm1": VaG_MECP_cm1,
    "VaG_MECP_kJmol": VaG_MECP_kJmol,
    "VaG_MECP_barrier_label": VaG_MECP_barrier_label,
    "E_MECP": E_MECP,
    "E_MECP_cm1": E_MECP_cm1,
    "Estep": Estep,
    "maxn_py": maxn_py,
    "binX": binX,
    "E_bins_cm1": E_bins_cm1,
    "E_LZ_cm1": E_LZ_cm1,
    "epsilon_rc_cm1": epsilon_rc_cm1,
    "epsilon_rc_positive_cm1": epsilon_rc_positive_cm1,
    "epsilon_rc_max_cm1": epsilon_rc_max_cm1,
    "E_above_MECP_cm1": E_above_MECP_cm1,
    "dE_cm1": dE_cm1,
    "nE": nE,
    "E_min_cm1": E_min_cm1,
    "E_max_requested_cm1": E_max_requested_cm1,
    "E_max_cm1": E_max_cm1,
    "E_max_nast_cm1": E_max_nast_cm1,
    "h_SI": h_SI,
    "c_SI": c_SI,
    "amu_to_kg": amu_to_kg,
    "bohr_m": bohr_m,
    "bohr_to_m": bohr_to_m,
    "mu_kg": mu_kg,
    "E_excess_J": E_excess_J,
    "epsilon_rc_J": epsilon_rc_J,
    "v_m_s": v_m_s,
    "v_bohr_s": v_bohr_s,
    "v_bohr_s_safe": v_bohr_s_safe,
    "velocity_floor": velocity_floor,
    "local_step7": local_step7,
    "remote_step7": remote_step7,
    "summary_file_step7": summary_file_step7,
    "grid_file_step7": grid_file_step7,
    "velocity_plot_file": velocity_plot_file,
    "remote_summary_file_step7": remote_summary_file_step7,
    "remote_grid_file_step7": remote_grid_file_step7,
    "remote_velocity_plot_file": remote_velocity_plot_file
})

# ============================================================
# Final summary
# ============================================================

section("STEP 7 SUMMARY")

print(f"VaG_MECP_electronic_cm1           : {VaG_MECP_electronic_cm1:.6f}")
print(f"ZPE_REF_from_freq_cm1             : {ZPE_REF_from_freq_cm1:.6f}")
print(f"ZPE_MECP_effhess_cm1              : {ZPE_MECP_effhess_cm1:.6f}")
print(f"Delta_ZPE_MECP_minus_REF_cm1      : {Delta_ZPE_MECP_minus_REF_cm1:.6f}")
print(f"VaG_MECP_cm1 active barrier       : {VaG_MECP_cm1:.6f}")
print(f"reduced_mass_amu                  : {reduced_mass_amu:.8f}")
print(f"DELTAF_PARALLEL_EH_PER_BOHR       : {DELTAF_PARALLEL_EH_PER_BOHR:.12e}")
print(f"GRADMEAN_EH_PER_BOHR              : {GRADMEAN_EH_PER_BOHR:.12e}")
print(f"NAST energy spacing               : {Estep:.3f} cm^-1")
print(f"NAST energy grid                  : {E_min_cm1:.3f} to {E_max_cm1:.3f} cm^-1")
print(f"Requested RC energy above MECP    : 0.000 to {epsilon_rc_max_cm1:.3f} cm^-1")
print(f"Velocity                          : {np.nanmin(v_m_s):.6e} to {np.nanmax(v_m_s):.6e} m/s")
print(f"Velocity                          : {np.nanmin(v_bohr_s):.6e} to {np.nanmax(v_bohr_s):.6e} Bohr/s")

print("\nImportant variables available for later steps:")
print("  VaG_MECP_electronic_cm1")
print("  VaG_MECP_ZPE_corrected_cm1")
print("  VaG_MECP_cm1")
print("  VaG_MECP_kJmol")
print("  ZPE_REF_from_freq_cm1")
print("  ZPE_MECP_effhess_cm1")
print("  Delta_ZPE_MECP_minus_REF_cm1")
print("  Estep")
print("  maxn_py")
print("  binX")
print("  E_bins_cm1")
print("  E_LZ_cm1")
print("  epsilon_rc_cm1")
print("  epsilon_rc_positive_cm1")
print("  epsilon_rc_max_cm1")
print("  E_above_MECP_cm1")
print("  dE_cm1")
print("  E_max_requested_cm1")
print("  E_max_cm1")
print("  E_max_nast_cm1")
print("  v_m_s")
print("  v_bohr_s")
print("  v_bohr_s_safe")
print("  reduced_mass_amu")
print("  mu_kg")
print("  DELTAF_PARALLEL_EH_PER_BOHR")
print("  GRADMEAN_EH_PER_BOHR")
print("  summary_file_step7")
print("  grid_file_step7")
print("  velocity_plot_file")

print("\nSTEP 7 COMPLETED SUCCESSFULLY.\n")


#%% STEP 8. CLUSTER EFFECTIVE + INTERMEDIATE + MS-RESOLVED LZ/WC PROBABILITIES

import os
import posixpath
import numpy as np
import matplotlib.pyplot as plt
from scipy.special import airy

print(r'''
====================================================================
 STEP 8 | CLUSTER VERSION
 Effective, intermediate, and Ms-resolved LZ/WC probabilities
====================================================================

Computes on the common 1 cm^-1 NAST grid created in Step 7:
  1. Effective LZ/WC probabilities using H_SO_cm
  2. Intermediate LZ/WC probabilities using Eq. 6
  3. MS-specific LZ/WC probabilities from individual SOC matrix elements

The Step 7 total-energy and velocity arrays are used directly.
Step 8 does not define a separate probability grid or velocity grid.

No sum over MS-specific channels is formed here.
Symmetry-equivalent channels with identical |H_SO| values are calculated
individually but represented by one shared plotting curve. Their labels are
listed with commas, never with plus signs, to make clear that no summation
has been performed.
''')

required_vars_step8 = [
    "jobname",
    "H_SO_cm",
    "DELTAF_PARALLEL_EH_PER_BOHR",
    "GRADMEAN_EH_PER_BOHR",
    "VaG_MECP_cm1",
    "E_bins_cm1",
    "v_m_s",
    "v_bohr_s",
    "Estep",
    "maxn_py",
    "binX",
    "reduced_mass_amu",
    "local_base",
    "remote_base"
]

for var in required_vars_step8:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Steps 1–7 first."
        )

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = "MECP_ONLY"


def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def spin_symbol(S_value):
    S_round = round(float(S_value), 1)

    spin_map = {
        0.0: "S",
        0.5: "D",
        1.0: "T",
        1.5: "Q",
        2.0: "Qu",
        2.5: "Se",
        3.0: "Sep",
        3.5: "Oct"
    }

    return spin_map.get(S_round, f"S={S_round}")


def abs_ms_label(Ms_abs):
    Ms_abs = float(Ms_abs)

    if abs(Ms_abs - round(Ms_abs)) < 1.0e-8:
        return str(int(round(Ms_abs)))

    return f"{Ms_abs:.1f}".replace(".", "p")


def ms_tex_value(x):
    x = float(x)

    if abs(x - round(x)) < 1.0e-8:
        return str(int(round(x)))

    if abs(abs(x) - 0.5) < 1.0e-8:
        return r"\frac{1}{2}" if x > 0 else r"-\frac{1}{2}"

    if abs(abs(x) - 1.5) < 1.0e-8:
        return r"\frac{3}{2}" if x > 0 else r"-\frac{3}{2}"

    if abs(abs(x) - 2.5) < 1.0e-8:
        return r"\frac{5}{2}" if x > 0 else r"-\frac{5}{2}"

    if abs(abs(x) - 3.5) < 1.0e-8:
        return r"\frac{7}{2}" if x > 0 else r"-\frac{7}{2}"

    return f"{x:.1f}"


def channel_tex_label(Ms_low_value, Ms_high_value):
    return (
        r"$P^{"
        + ms_tex_value(Ms_low_value)
        + ","
        + ms_tex_value(Ms_high_value)
        + r"}$"
    )


def make_group_channel_legend(prefix, group_data):
    """
    Build a legend for one representative MS-specific curve.

    Channels in the same group have equal |H_SO| within the grouping
    tolerance and therefore generate identical probability curves.  Their
    labels are separated by commas, not plus signs, because the channels are
    not summed.
    """
    members = group_data.get("channel_members", [])
    H_abs = float(group_data["H_abs_cm1"])

    if len(members) == 0:
        return f"{prefix} MS-specific, |H|={H_abs:.1f}"

    channel_terms = []
    for member in members:
        channel_terms.append(
            r"P^{"
            + ms_tex_value(member["Ms_low"])
            + ","
            + ms_tex_value(member["Ms_high"])
            + r"}"
        )

    if len(channel_terms) <= 4:
        joined = ", ".join(channel_terms)
    else:
        joined = ", ".join(channel_terms[:4]) + r", \ldots"

    return f"{prefix} ${{{joined}}}$, |H|={H_abs:.1f}"


def find_matching_hso_group(groups, H_abs, atol=1.0e-8, rtol=1.0e-10):
    """Return the existing group key whose |H_SO| matches H_abs."""
    for key, data in groups.items():
        if np.isclose(
            float(H_abs),
            float(data["H_abs_cm1"]),
            atol=atol,
            rtol=rtol
        ):
            return key
    return None


# ============================================================
# Constants and active quantities
# ============================================================

section("STEP 8 CONSTANTS AND ACTIVE INPUTS")

h_SI = 6.62607015e-34
c_SI = 2.99792458e8
Eh_to_J = 4.3597447222071e-18
autocm = 219474.6313705
amu_to_kg = 1.66053906660e-27
amu_to_au = 1822.8884853323708
bohr_m = 5.29177210903e-11

# Use the Step 7 NAST grid directly.
E_bins_cm1 = np.asarray(E_bins_cm1, dtype=float).reshape(-1)
E_LZ_cm1 = E_bins_cm1.copy()

v_m_s_step7 = np.asarray(v_m_s, dtype=float).reshape(-1)
v_bohr_s_step7 = np.asarray(v_bohr_s, dtype=float).reshape(-1)

E_MECP_cm1 = float(VaG_MECP_cm1)
E_MECP = E_MECP_cm1

DeltaF_parallel = abs(float(DELTAF_PARALLEL_EH_PER_BOHR))
gradmean = abs(float(GRADMEAN_EH_PER_BOHR))

mu_kg = float(reduced_mass_amu) * amu_to_kg
mu_au = float(reduced_mass_amu) * amu_to_au

Estep = float(Estep)
maxn_py = int(maxn_py)
binX = int(binX)

if E_bins_cm1.size == 0:
    raise RuntimeError("E_bins_cm1 is empty. Run the modified Step 7 first.")

if E_bins_cm1.size != maxn_py + 1:
    raise RuntimeError(
        f"E_bins_cm1 has length {E_bins_cm1.size}, "
        f"but maxn_py + 1 is {maxn_py + 1}."
    )

if v_m_s_step7.size != E_bins_cm1.size:
    raise RuntimeError(
        "Step 7 v_m_s length does not match E_bins_cm1."
    )

if v_bohr_s_step7.size != E_bins_cm1.size:
    raise RuntimeError(
        "Step 7 v_bohr_s length does not match E_bins_cm1."
    )

if Estep <= 0.0 or not np.isfinite(Estep):
    raise RuntimeError("Invalid Estep from Step 7.")

expected_grid = np.arange(maxn_py + 1, dtype=float) * Estep

if not np.allclose(E_bins_cm1, expected_grid, rtol=0.0, atol=1.0e-12):
    raise RuntimeError(
        "E_bins_cm1 is not the common uniform NAST grid from Step 7."
    )

expected_binX = int(np.ceil(E_MECP_cm1 / Estep))

if binX != expected_binX:
    raise RuntimeError(
        f"Step 7 binX={binX}, but ceil(E_MECP/Estep)={expected_binX}."
    )

if DeltaF_parallel <= 0.0 or not np.isfinite(DeltaF_parallel):
    raise RuntimeError("Invalid DELTAF_PARALLEL_EH_PER_BOHR.")

if gradmean <= 0.0 or not np.isfinite(gradmean):
    raise RuntimeError("Invalid GRADMEAN_EH_PER_BOHR.")

if mu_kg <= 0.0 or not np.isfinite(mu_kg):
    raise RuntimeError("Invalid reduced_mass_amu.")

velocity_floor = float(globals().get("velocity_floor", 1.0e-12))

# Independent consistency check using the exact Step 9 velocity definition.
E_excess_J_check = (
    (E_bins_cm1 - E_MECP_cm1)
    * 100.0
    * h_SI
    * c_SI
)

v_m_s_check = np.zeros_like(E_bins_cm1)
mask_velocity_check = E_excess_J_check > 0.0

v_m_s_check[mask_velocity_check] = np.sqrt(
    2.0 * E_excess_J_check[mask_velocity_check] / mu_kg
)

v_bohr_s_check = v_m_s_check / bohr_m

if not np.allclose(
    v_m_s_step7,
    v_m_s_check,
    rtol=1.0e-12,
    atol=1.0e-12
):
    raise RuntimeError(
        "Step 7 v_m_s does not match the Step 9 velocity definition."
    )

if not np.allclose(
    v_bohr_s_step7,
    v_bohr_s_check,
    rtol=1.0e-12,
    atol=1.0e-6
):
    raise RuntimeError(
        "Step 7 v_bohr_s does not match the Step 9 velocity definition."
    )

# These are the authoritative velocity arrays used throughout Step 8.
v_m_s = v_m_s_step7.copy()
v_bohr_s = v_bohr_s_step7.copy()

print(f"RUN_MODE                         : {RUN_MODE}")
print(f"workflow_mode                    : {workflow_mode}")
print(f"H_SO_cm effective                : {float(H_SO_cm):.12f} cm^-1")
print(f"E_MECP_cm1 active threshold      : {E_MECP_cm1:.12f} cm^-1")
print(f"Estep                            : {Estep:.6f} cm^-1")
print(f"maxn_py                          : {maxn_py}")
print(f"binX                             : {binX}")
print(f"DeltaF_parallel                  : {DeltaF_parallel:.12e} Eh/Bohr")
print(f"gradmean                         : {gradmean:.12e} Eh/Bohr")
print(f"reduced_mass_amu                 : {float(reduced_mass_amu):.8f} amu")
print(f"mu_kg                            : {mu_kg:.12e} kg")
print(f"mu_au                            : {mu_au:.12e} a.u.")
print(f"Energy grid points               : {E_bins_cm1.size}")
print(
    f"Energy grid range                : "
    f"{E_bins_cm1[0]:.6f} to {E_bins_cm1[-1]:.6f} cm^-1"
)
print("Velocity source                  : Step 7 common NAST grid")
print("Velocity consistency check       : PASSED")


# ============================================================
# Probability functions
# ============================================================

def compute_LZ_for_HSO_cm(
    H_cm,
    E_grid_cm1=None,
    v_bohr_s_grid=None,
    v_m_s_grid=None
):
    """
    Compute an LZ probability on a supplied energy/velocity grid.

    By default, this function uses the common Step 7 NAST grid and the
    Step 7 velocity arrays directly. A custom grid is accepted only when
    matching velocity arrays are supplied explicitly.
    """
    if E_grid_cm1 is None:
        E_grid_cm1 = E_bins_cm1
        v_bohr_s_grid = v_bohr_s
        v_m_s_grid = v_m_s

    else:
        E_grid_cm1 = np.asarray(
            E_grid_cm1,
            dtype=float
        ).reshape(-1)

        if v_bohr_s_grid is None or v_m_s_grid is None:
            raise ValueError(
                "A custom energy grid requires matching v_bohr_s_grid "
                "and v_m_s_grid arrays. Step 8 does not independently "
                "redefine velocity."
            )

        v_bohr_s_grid = np.asarray(
            v_bohr_s_grid,
            dtype=float
        ).reshape(-1)

        v_m_s_grid = np.asarray(
            v_m_s_grid,
            dtype=float
        ).reshape(-1)

    if E_grid_cm1.size != v_bohr_s_grid.size:
        raise ValueError(
            "Energy and Bohr/s velocity arrays have different lengths."
        )

    if E_grid_cm1.size != v_m_s_grid.size:
        raise ValueError(
            "Energy and m/s velocity arrays have different lengths."
        )

    H_cm = float(abs(H_cm))
    H_J = h_SI * c_SI * H_cm * 100.0

    P = np.zeros_like(E_grid_cm1, dtype=float)
    gamma = np.full_like(E_grid_cm1, np.nan, dtype=float)

    mask = (
        np.isfinite(E_grid_cm1)
        & np.isfinite(v_bohr_s_grid)
        & (E_grid_cm1 >= E_MECP_cm1)
        & (v_bohr_s_grid > velocity_floor)
    )

    dDeltaE_dt_Eh_s = DeltaF_parallel * v_bohr_s_grid
    dDeltaE_dt_J_s = dDeltaE_dt_Eh_s * Eh_to_J

    gamma[mask] = (
        4.0 * np.pi**2 * H_J**2
        / (h_SI * dDeltaE_dt_J_s[mask])
    )

    P[mask] = 1.0 - np.exp(-2.0 * gamma[mask])
    P[~np.isfinite(P)] = 0.0
    P = np.clip(P, 0.0, 1.0)

    return (
        P,
        gamma,
        np.asarray(v_bohr_s_grid, dtype=float).copy(),
        np.asarray(v_m_s_grid, dtype=float).copy()
    )


def compute_WC_for_HSO_cm(H_cm, E_grid_cm1=None):
    if E_grid_cm1 is None:
        E_grid_cm1 = E_bins_cm1

    E_grid_cm1 = np.asarray(E_grid_cm1, dtype=float).reshape(-1)

    H_Eh = float(abs(H_cm)) / autocm
    E_excess_Eh = (E_grid_cm1 - E_MECP_cm1) / autocm

    P = np.zeros_like(E_grid_cm1)
    airy_arg = np.full_like(E_grid_cm1, np.nan)
    Ai = np.full_like(E_grid_cm1, np.nan)

    mask = (
        np.isfinite(E_grid_cm1)
        & np.isfinite(E_excess_Eh)
    )

    prefactor = (
        4.0
        * np.pi**2
        * H_Eh**2
        * (
            2.0 * mu_au
            / (gradmean * DeltaF_parallel)
        ) ** (2.0 / 3.0)
    )

    scale = (
        2.0
        * mu_au
        * DeltaF_parallel**2
        / gradmean**4
    ) ** (1.0 / 3.0)

    airy_arg[mask] = -E_excess_Eh[mask] * scale
    Ai[mask] = airy(airy_arg[mask])[0]

    P[mask] = prefactor * Ai[mask] ** 2
    P[~np.isfinite(P)] = 0.0
    P = np.clip(P, 0.0, 1.0)

    return P, airy_arg, Ai


# ============================================================
# Effective probabilities
# ============================================================

section("EFFECTIVE LZ/WC PROBABILITIES")

P_LZ_effective, gamma_LZ_effective, v_bohr_s, v_m_s = (
    compute_LZ_for_HSO_cm(H_SO_cm)
)

P_WC_effective, airy_arg_WC_effective, Ai_WC_effective = (
    compute_WC_for_HSO_cm(H_SO_cm)
)

P_LZ_micro = P_LZ_effective
gamma_LZ = gamma_LZ_effective
P_WC_micro = P_WC_effective

print(f"P_LZ_effective range             : {np.nanmin(P_LZ_effective):.6e} to {np.nanmax(P_LZ_effective):.6e}")
print(f"P_WC_effective range             : {np.nanmin(P_WC_effective):.6e} to {np.nanmax(P_WC_effective):.6e}")


# ============================================================
# Ms-resolved + intermediate probabilities
# ============================================================

section("MS-RESOLVED AND INTERMEDIATE PROBABILITIES")

P_LZ_channels = {}
P_LZ_channel_groups = {}

P_WC_channels = {}
P_WC_channel_groups = {}

P_LZ_intermediate = {}
P_WC_intermediate = {}

SOC_EFFECTIVE_ONLY = bool(globals().get("SOC_EFFECTIVE_ONLY", False))
SOC_MATRIX_CHANNELS_AVAILABLE = bool(globals().get("SOC_MATRIX_CHANNELS_AVAILABLE", True))

if SOC_EFFECTIVE_ONLY or not SOC_MATRIX_CHANNELS_AVAILABLE:
    SOC_channel_matrix_cm1 = None
    SOC_channel_matrix_label = "effective-only scalar SOC"

    print("Effective-only SOC mode detected.")
    print("MS-specific and intermediate probability curves will not be generated.")

else:
    if "SOC_Ms_matrix_scaled_cm1" in globals():
        SOC_channel_matrix_cm1 = np.asarray(
            SOC_Ms_matrix_scaled_cm1,
            dtype=complex
        )
        SOC_channel_matrix_label = "SOC_Ms_matrix_scaled_cm1"

    elif "SOC_Ms_matrix_cm1" in globals():
        SOC_channel_matrix_cm1 = np.asarray(
            SOC_Ms_matrix_cm1,
            dtype=complex
        )
        SOC_channel_matrix_label = "SOC_Ms_matrix_cm1"

    else:
        SOC_channel_matrix_cm1 = None
        SOC_channel_matrix_label = None

if SOC_channel_matrix_cm1 is not None:

    if "Ms_low" not in globals() or "Ms_high" not in globals():
        raise RuntimeError("Ms_low/Ms_high are missing. Run cluster Step 2 first.")

    if "S_low" not in globals() or "S_high" not in globals():
        raise RuntimeError("S_low/S_high are missing. Run cluster Step 2 first.")

    Ms_low = np.asarray(Ms_low, dtype=float)
    Ms_high = np.asarray(Ms_high, dtype=float)

    if SOC_channel_matrix_cm1.shape != (len(Ms_low), len(Ms_high)):
        raise RuntimeError(
            "SOC matrix shape does not match Ms_low/Ms_high dimensions: "
            f"{SOC_channel_matrix_cm1.shape} vs ({len(Ms_low)}, {len(Ms_high)})."
        )

    # --------------------------------------------------------
    # MS-specific probabilities
    # --------------------------------------------------------
    for i, Ms_i in enumerate(Ms_low):
        for j, Ms_j in enumerate(Ms_high):

            H_ij = SOC_channel_matrix_cm1[i, j]
            H_abs = float(abs(H_ij))

            if H_abs <= 1.0e-10:
                continue

            channel_symbol = channel_tex_label(Ms_i, Ms_j)

            label = (
                f"S{S_low:.1f}_Ms{Ms_i:+.1f}"
                f"__S{S_high:.1f}_Ms{Ms_j:+.1f}"
            )

            P_lz_ij, gamma_lz_ij, _, _ = compute_LZ_for_HSO_cm(H_abs)
            P_wc_ij, airy_arg_wc_ij, Ai_wc_ij = compute_WC_for_HSO_cm(H_abs)

            P_LZ_channels[label] = {
                "P": P_lz_ij,
                "gamma": gamma_lz_ij,
                "H_complex_cm1": H_ij,
                "H_abs_cm1": H_abs,
                "S_low": S_low,
                "S_high": S_high,
                "Ms_low": Ms_i,
                "Ms_high": Ms_j,
                "matrix_index": (i, j),
                "source_matrix": SOC_channel_matrix_label,
                "channel_symbol": channel_symbol
            }

            P_WC_channels[label] = {
                "P": P_wc_ij,
                "airy_arg": airy_arg_wc_ij,
                "Ai": Ai_wc_ij,
                "H_complex_cm1": H_ij,
                "H_abs_cm1": H_abs,
                "S_low": S_low,
                "S_high": S_high,
                "Ms_low": Ms_i,
                "Ms_high": Ms_j,
                "matrix_index": (i, j),
                "source_matrix": SOC_channel_matrix_label,
                "channel_symbol": channel_symbol
            }

    # --------------------------------------------------------
    # Symmetry/equal-|H_SO| representative plotting groups
    # --------------------------------------------------------
    # Every nonzero MS-specific channel remains available separately in
    # P_LZ_channels and P_WC_channels.  For plotting only, channels whose
    # |H_SO| values are equal within a tight numerical tolerance share one
    # representative curve because their probability arrays are identical.
    # No probability is added, averaged, or multiplied by the group size.
    hso_group_atol_cm1 = float(
        globals().get("MS_CHANNEL_HSO_GROUP_ATOL_CM1", 1.0e-8)
    )
    hso_group_rtol = float(
        globals().get("MS_CHANNEL_HSO_GROUP_RTOL", 1.0e-10)
    )

    for label, data in P_LZ_channels.items():
        H_abs = float(data["H_abs_cm1"])
        group_key = find_matching_hso_group(
            P_LZ_channel_groups,
            H_abs,
            atol=hso_group_atol_cm1,
            rtol=hso_group_rtol
        )

        if group_key is None:
            group_key = f"Hgroup_{len(P_LZ_channel_groups) + 1:03d}"
            P_LZ_channel_groups[group_key] = {
                "H_abs_cm1": H_abs,
                "labels": [],
                "channel_symbols": [],
                "channel_members": [],
                "representative_label": label,
                "P_representative": data["P"],
                "gamma_representative": data["gamma"],
                "multiplicity": 0,
                "source_matrix": SOC_channel_matrix_label,
                "grouping_rule": "equal |H_SO|; representative only; no sum"
            }

        group = P_LZ_channel_groups[group_key]
        group["labels"].append(label)
        group["channel_symbols"].append(data["channel_symbol"])
        group["channel_members"].append({
            "label": label,
            "S_low": float(data["S_low"]),
            "Ms_low": float(data["Ms_low"]),
            "S_high": float(data["S_high"]),
            "Ms_high": float(data["Ms_high"]),
            "H_abs_cm1": H_abs
        })
        group["multiplicity"] += 1

    for label, data in P_WC_channels.items():
        H_abs = float(data["H_abs_cm1"])
        group_key = find_matching_hso_group(
            P_WC_channel_groups,
            H_abs,
            atol=hso_group_atol_cm1,
            rtol=hso_group_rtol
        )

        if group_key is None:
            group_key = f"Hgroup_{len(P_WC_channel_groups) + 1:03d}"
            P_WC_channel_groups[group_key] = {
                "H_abs_cm1": H_abs,
                "labels": [],
                "channel_symbols": [],
                "channel_members": [],
                "representative_label": label,
                "P_representative": data["P"],
                "airy_arg_representative": data["airy_arg"],
                "Ai_representative": data["Ai"],
                "multiplicity": 0,
                "source_matrix": SOC_channel_matrix_label,
                "grouping_rule": "equal |H_SO|; representative only; no sum"
            }

        group = P_WC_channel_groups[group_key]
        group["labels"].append(label)
        group["channel_symbols"].append(data["channel_symbol"])
        group["channel_members"].append({
            "label": label,
            "S_low": float(data["S_low"]),
            "Ms_low": float(data["Ms_low"]),
            "S_high": float(data["S_high"]),
            "Ms_high": float(data["Ms_high"]),
            "H_abs_cm1": H_abs
        })
        group["multiplicity"] += 1

    # --------------------------------------------------------
    # Intermediate probabilities, Eq. 6
    # One curve per unique |Ms_low| group.
    # No weighted sum is computed here.
    # --------------------------------------------------------
    abs_ms_groups = sorted(set([round(abs(float(x)), 8) for x in Ms_low]))

    for abs_Ms in abs_ms_groups:
        row_indices = [
            i for i, Ms_i in enumerate(Ms_low)
            if abs(abs(float(Ms_i)) - abs_Ms) < 1.0e-8
        ]

        H_rows = []

        for i in row_indices:
            row = SOC_channel_matrix_cm1[i, :]
            H_row = float(np.sqrt(np.sum(np.abs(row) ** 2)))
            H_rows.append(H_row)

        H_rows = np.asarray(H_rows, dtype=float)
        H_rows = H_rows[np.isfinite(H_rows) & (H_rows > 1.0e-10)]

        if H_rows.size == 0:
            continue

        H_int = float(np.sqrt(np.mean(H_rows ** 2)))

        label = f"INT_absMs{abs_ms_label(abs_Ms)}"

        P_lz_int, gamma_lz_int, _, _ = compute_LZ_for_HSO_cm(H_int)
        P_wc_int, airy_arg_wc_int, Ai_wc_int = compute_WC_for_HSO_cm(H_int)

        P_LZ_intermediate[label] = {
            "P": P_lz_int,
            "gamma": gamma_lz_int,
            "H_int_cm1": H_int,
            "H_rows_cm1": H_rows,
            "abs_Ms_low": abs_Ms,
            "degeneracy": len(row_indices),
            "row_indices": row_indices,
            "source_matrix": SOC_channel_matrix_label
        }

        P_WC_intermediate[label] = {
            "P": P_wc_int,
            "airy_arg": airy_arg_wc_int,
            "Ai": Ai_wc_int,
            "H_int_cm1": H_int,
            "H_rows_cm1": H_rows,
            "abs_Ms_low": abs_Ms,
            "degeneracy": len(row_indices),
            "row_indices": row_indices,
            "source_matrix": SOC_channel_matrix_label
        }

    print(f"SOC channel matrix source          : {SOC_channel_matrix_label}")
    print(f"Nonzero Ms-resolved channels       : {len(P_LZ_channels)}")
    print(f"Unique plotted |H_SO| curves       : {len(P_LZ_channel_groups)}")
    print(f"Intermediate |Ms_low| curves       : {len(P_LZ_intermediate)}")

    if P_LZ_intermediate:
        print("\nIntermediate SOC values:")
        for key, data in sorted(
            P_LZ_intermediate.items(),
            key=lambda x: x[1]["abs_Ms_low"]
        ):
            print(
                f"  {key:14s}  |Ms_low| = {data['abs_Ms_low']:.3f}  "
                f"deg = {data['degeneracy']:2d}  "
                f"H_int = {data['H_int_cm1']:.6f} cm^-1"
            )

    if P_LZ_channel_groups:
        print("\nMS-specific plotting groups (representative only; no sums):")
        for key, data in sorted(
            P_LZ_channel_groups.items(),
            key=lambda x: x[1]["H_abs_cm1"],
            reverse=True
        ):
            readable = ", ".join(
                [
                    f"P^{{{ms_tex_value(m['Ms_low'])},{ms_tex_value(m['Ms_high'])}}}"
                    for m in data.get("channel_members", [])
                ]
            )
            print(
                f"  {key:12s}  {readable:30s}  "
                f"|H| = {data['H_abs_cm1']:.6f} cm^-1"
            )

else:
    print("No Ms-resolved SOC matrix found. Only effective probabilities were calculated.")


# ============================================================
# Plot probability curves
# ============================================================

section("PLOTTING STEP 8 PROBABILITY CURVES")

local_step8 = os.path.join(local_base, "LZ_WC_probabilities")
os.makedirs(local_step8, exist_ok=True)

remote_step8 = posixpath.join(remote_base, "LZ_WC_probabilities")

if "remote_mkdir_p" in globals() and "sftp" in globals():
    try:
        remote_mkdir_p(sftp, remote_step8)
    except Exception:
        pass

lz_plot_file_step8 = os.path.join(
    local_step8,
    f"{jobname}_Step8_LZ_probabilities.png"
)

wc_plot_file_step8 = os.path.join(
    local_step8,
    f"{jobname}_Step8_WC_probabilities.png"
)

# Use the actual common NAST probability arrays. No separate plotting
# grid and no separate velocity calculation are introduced.
lz_plot_min_cm1 = max(
    float(E_bins_cm1[0]),
    E_MECP_cm1 - 1000.0
)

wc_plot_min_cm1 = max(
    float(E_bins_cm1[0]),
    E_MECP_cm1 - 1500.0
)

mask_plot_lz = E_bins_cm1 >= lz_plot_min_cm1
mask_plot_wc = E_bins_cm1 >= wc_plot_min_cm1

# ----------------------------
# LZ plot
# ----------------------------
plt.figure(figsize=(9.2, 5.2))

plt.plot(
    E_bins_cm1[mask_plot_lz],
    P_LZ_effective[mask_plot_lz],
    color="k",
    linestyle=":",
    linewidth=2.5,
    label=f"LZ $P^{{eff}}$, HSO = {H_SO_cm:.1f}"
)

if P_LZ_intermediate:
    for key, data in sorted(
        P_LZ_intermediate.items(),
        key=lambda x: x[1]["abs_Ms_low"]
    ):
        plt.plot(
            E_bins_cm1[mask_plot_lz],
            data["P"][mask_plot_lz],
            linewidth=2.0,
            linestyle="--",
            label=(
                f"LZ $P^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
                f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
            )
        )

if P_LZ_channel_groups:
    for key, data in sorted(
        P_LZ_channel_groups.items(),
        key=lambda x: x[1]["H_abs_cm1"],
        reverse=True
    ):
        plt.plot(
            E_bins_cm1[mask_plot_lz],
            data["P_representative"][mask_plot_lz],
            linewidth=1.4,
            label=make_group_channel_legend("LZ", data)
        )

plt.axvline(
    E_MECP_cm1,
    color="k",
    linestyle="--",
    linewidth=1.5,
    label="ZPE-corrected MECP"
)

plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("Landau-Zener probability")
plt.title("Effective, Intermediate, and MS-specific LZ Probabilities")
plt.grid(False)
plt.legend(fontsize=7)
plt.tight_layout()
plt.savefig(lz_plot_file_step8, dpi=300)
plt.show()

# ----------------------------
# WC plot
# ----------------------------
plt.figure(figsize=(9.2, 5.2))

plt.plot(
    E_bins_cm1[mask_plot_wc],
    P_WC_effective[mask_plot_wc],
    color="k",
    linestyle=":",
    linewidth=2.5,
    label=f"WC $P^{{eff}}$, HSO = {H_SO_cm:.1f}"
)

if P_WC_intermediate:
    for key, data in sorted(
        P_WC_intermediate.items(),
        key=lambda x: x[1]["abs_Ms_low"]
    ):
        plt.plot(
            E_bins_cm1[mask_plot_wc],
            data["P"][mask_plot_wc],
            linewidth=2.0,
            linestyle="--",
            label=(
                f"WC $P^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
                f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
            )
        )

if P_WC_channel_groups:
    for key, data in sorted(
        P_WC_channel_groups.items(),
        key=lambda x: x[1]["H_abs_cm1"],
        reverse=True
    ):
        plt.plot(
            E_bins_cm1[mask_plot_wc],
            data["P_representative"][mask_plot_wc],
            linewidth=1.4,
            label=make_group_channel_legend("WC", data)
        )

plt.axvline(
    E_MECP_cm1,
    color="k",
    linestyle="--",
    linewidth=1.5,
    label="ZPE-corrected MECP"
)

plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("Weak-coupling probability")
plt.title("Effective, Intermediate, and MS-specific WC Probabilities")
plt.grid(False)
plt.legend(fontsize=7)
plt.tight_layout()
plt.savefig(wc_plot_file_step8, dpi=300)
plt.show()

print(f"LZ probability plot saved locally : {lz_plot_file_step8}")
print(f"WC probability plot saved locally : {wc_plot_file_step8}")


# ============================================================
# Save Step 8 numerical outputs
# ============================================================

section("SAVING STEP 8 OUTPUTS")

summary_file_step8 = os.path.join(
    local_step8,
    f"{jobname}_Step8_probability_summary.txt"
)

effective_grid_file_step8 = os.path.join(
    local_step8,
    f"{jobname}_Step8_effective_probability_grid.txt"
)

channel_summary_file_step8 = os.path.join(
    local_step8,
    f"{jobname}_Step8_channel_probability_summary.txt"
)

intermediate_summary_file_step8 = os.path.join(
    local_step8,
    f"{jobname}_Step8_intermediate_probability_summary.txt"
)

summary_lines = []

summary_lines.append("Step 8 effective, intermediate, and Ms-resolved LZ/WC probabilities")
summary_lines.append(f"Run mode = {RUN_MODE}")
summary_lines.append(f"Workflow mode = {workflow_mode}")
summary_lines.append("")
summary_lines.append("[ACTIVE_INPUTS]")
summary_lines.append(f"H_SO_cm = {float(H_SO_cm):.12f}")
summary_lines.append(f"E_MECP_cm1 = {E_MECP_cm1:.12f}")
summary_lines.append(f"VaG_MECP_cm1 = {float(VaG_MECP_cm1):.12f}")
summary_lines.append(f"DELTAF_PARALLEL_EH_PER_BOHR = {DeltaF_parallel:.12e}")
summary_lines.append(f"GRADMEAN_EH_PER_BOHR = {gradmean:.12e}")
summary_lines.append(f"reduced_mass_amu = {float(reduced_mass_amu):.12f}")
summary_lines.append(f"mu_kg = {mu_kg:.12e}")
summary_lines.append(f"mu_au = {mu_au:.12e}")
summary_lines.append(f"Estep = {Estep:.12f}")
summary_lines.append(f"maxn_py = {maxn_py}")
summary_lines.append(f"binX = {binX}")
summary_lines.append(f"energy_grid_source = Step 7 E_bins_cm1")
summary_lines.append(f"velocity_source = Step 7 v_m_s and v_bohr_s")
summary_lines.append("")
summary_lines.append("[EFFECTIVE_PROBABILITY_RANGES]")
summary_lines.append(f"P_LZ_effective_min = {np.nanmin(P_LZ_effective):.12e}")
summary_lines.append(f"P_LZ_effective_max = {np.nanmax(P_LZ_effective):.12e}")
summary_lines.append(f"P_WC_effective_min = {np.nanmin(P_WC_effective):.12e}")
summary_lines.append(f"P_WC_effective_max = {np.nanmax(P_WC_effective):.12e}")
summary_lines.append("")
summary_lines.append("[INTERMEDIATE]")
summary_lines.append(f"intermediate_LZ_curves = {len(P_LZ_intermediate)}")
summary_lines.append(f"intermediate_WC_curves = {len(P_WC_intermediate)}")
summary_lines.append("")
summary_lines.append("[MS_SPECIFIC_CHANNELS]")
summary_lines.append(f"SOC_channel_matrix_label = {SOC_channel_matrix_label}")
summary_lines.append(f"nonzero_LZ_channels = {len(P_LZ_channels)}")
summary_lines.append(f"unique_plotted_LZ_channel_curves = {len(P_LZ_channel_groups)}")
summary_lines.append(f"nonzero_WC_channels = {len(P_WC_channels)}")
summary_lines.append(f"unique_plotted_WC_channel_curves = {len(P_WC_channel_groups)}")
summary_lines.append("")
summary_lines.append("[FILES]")
summary_lines.append(f"effective_grid_file_step8 = {effective_grid_file_step8}")
summary_lines.append(f"intermediate_summary_file_step8 = {intermediate_summary_file_step8}")
summary_lines.append(f"channel_summary_file_step8 = {channel_summary_file_step8}")
summary_lines.append(f"lz_plot_file_step8 = {lz_plot_file_step8}")
summary_lines.append(f"wc_plot_file_step8 = {wc_plot_file_step8}")

write_local_text(summary_file_step8, "\n".join(summary_lines) + "\n")

grid_lines = []
grid_lines.append(
    "E_bins_cm1  E_minus_MECP_cm1  v_m_s  v_bohr_s  "
    "P_LZ_effective  gamma_LZ_effective  P_WC_effective  "
    "airy_arg_WC_effective  Ai_WC_effective"
)

for E, vm, vb, Plz, gam, Pwc, arg, ai in zip(
    E_bins_cm1,
    v_m_s,
    v_bohr_s,
    P_LZ_effective,
    gamma_LZ_effective,
    P_WC_effective,
    airy_arg_WC_effective,
    Ai_WC_effective
):
    grid_lines.append(
        f"{E:18.10f} "
        f"{(E - E_MECP_cm1):18.10f} "
        f"{vm:18.10e} "
        f"{vb:18.10e} "
        f"{Plz:18.10e} "
        f"{gam:18.10e} "
        f"{Pwc:18.10e} "
        f"{arg:18.10e} "
        f"{ai:18.10e}"
    )

write_local_text(effective_grid_file_step8, "\n".join(grid_lines) + "\n")

intermediate_lines = []
intermediate_lines.append("Step 8 intermediate SOC probabilities")
intermediate_lines.append("")
intermediate_lines.append("[INTERMEDIATE_CURVES]")
intermediate_lines.append("label  abs_Ms_low  degeneracy  H_int_cm1  H_rows_cm1")

for label, data in sorted(
    P_LZ_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    hrows = ",".join([f"{x:.10f}" for x in data["H_rows_cm1"]])

    intermediate_lines.append(
        f"{label:20s} "
        f"{data['abs_Ms_low']:12.6f} "
        f"{data['degeneracy']:5d} "
        f"{data['H_int_cm1']:18.10f} "
        f"{hrows}"
    )

write_local_text(intermediate_summary_file_step8, "\n".join(intermediate_lines) + "\n")

channel_lines = []
channel_lines.append("Step 8 Ms-resolved SOC channel probabilities")
channel_lines.append("")
channel_lines.append("[LZ_CHANNEL_GROUPS]")
channel_lines.append("group_key  H_abs_cm1  degeneracy  symbols  labels")

for key, data in sorted(
    P_LZ_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    channel_lines.append(
        f"{key:16s} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['multiplicity']:5d} "
        f"{', '.join(data['channel_symbols']):30s} "
        + ",".join(data["labels"])
    )

channel_lines.append("")
channel_lines.append("[WC_CHANNEL_GROUPS]")
channel_lines.append("group_key  H_abs_cm1  degeneracy  symbols  labels")

for key, data in sorted(
    P_WC_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    channel_lines.append(
        f"{key:16s} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['multiplicity']:5d} "
        f"{', '.join(data['channel_symbols']):30s} "
        + ",".join(data["labels"])
    )

channel_lines.append("")
channel_lines.append("[INDIVIDUAL_CHANNELS]")
channel_lines.append(
    "symbol  label  H_real_cm1  H_imag_cm1  H_abs_cm1  "
    "S_low  Ms_low  S_high  Ms_high  matrix_i  matrix_j"
)

for label, data in sorted(
    P_LZ_channels.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    H_complex = data["H_complex_cm1"]
    i, j = data["matrix_index"]

    channel_lines.append(
        f"{data['channel_symbol']:20s} "
        f"{label:40s} "
        f"{H_complex.real:18.10f} "
        f"{H_complex.imag:18.10f} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['S_low']:8.3f} "
        f"{data['Ms_low']:8.3f} "
        f"{data['S_high']:8.3f} "
        f"{data['Ms_high']:8.3f} "
        f"{i:5d} "
        f"{j:5d}"
    )

write_local_text(channel_summary_file_step8, "\n".join(channel_lines) + "\n")

remote_summary_file_step8 = None
remote_effective_grid_file_step8 = None
remote_channel_summary_file_step8 = None
remote_intermediate_summary_file_step8 = None
remote_lz_plot_file_step8 = None
remote_wc_plot_file_step8 = None

if "sftp" in globals():
    try:
        remote_summary_file_step8 = posixpath.join(
            remote_step8,
            posixpath.basename(summary_file_step8)
        )
        remote_effective_grid_file_step8 = posixpath.join(
            remote_step8,
            posixpath.basename(effective_grid_file_step8)
        )
        remote_channel_summary_file_step8 = posixpath.join(
            remote_step8,
            posixpath.basename(channel_summary_file_step8)
        )
        remote_intermediate_summary_file_step8 = posixpath.join(
            remote_step8,
            posixpath.basename(intermediate_summary_file_step8)
        )
        remote_lz_plot_file_step8 = posixpath.join(
            remote_step8,
            posixpath.basename(lz_plot_file_step8)
        )
        remote_wc_plot_file_step8 = posixpath.join(
            remote_step8,
            posixpath.basename(wc_plot_file_step8)
        )

        sftp.put(summary_file_step8, remote_summary_file_step8)
        sftp.put(effective_grid_file_step8, remote_effective_grid_file_step8)
        sftp.put(channel_summary_file_step8, remote_channel_summary_file_step8)
        sftp.put(intermediate_summary_file_step8, remote_intermediate_summary_file_step8)
        sftp.put(lz_plot_file_step8, remote_lz_plot_file_step8)
        sftp.put(wc_plot_file_step8, remote_wc_plot_file_step8)

    except Exception:
        remote_summary_file_step8 = None
        remote_effective_grid_file_step8 = None
        remote_channel_summary_file_step8 = None
        remote_intermediate_summary_file_step8 = None
        remote_lz_plot_file_step8 = None
        remote_wc_plot_file_step8 = None

print(f"Local Step 8 summary              : {summary_file_step8}")
print(f"Local effective grid              : {effective_grid_file_step8}")
print(f"Local intermediate summary        : {intermediate_summary_file_step8}")
print(f"Local channel summary             : {channel_summary_file_step8}")
print(f"Local LZ plot                     : {lz_plot_file_step8}")
print(f"Local WC plot                     : {wc_plot_file_step8}")

if remote_summary_file_step8:
    print(f"Remote Step 8 summary             : {remote_summary_file_step8}")
    print(f"Remote effective grid             : {remote_effective_grid_file_step8}")
    print(f"Remote intermediate summary       : {remote_intermediate_summary_file_step8}")
    print(f"Remote channel summary            : {remote_channel_summary_file_step8}")
    print(f"Remote LZ plot                    : {remote_lz_plot_file_step8}")
    print(f"Remote WC plot                    : {remote_wc_plot_file_step8}")


# ============================================================
# Export variables for later steps
# ============================================================

globals().update({
    "RUN_MODE": RUN_MODE,
    "workflow_mode": workflow_mode,
    "h_SI": h_SI,
    "c_SI": c_SI,
    "Eh_to_J": Eh_to_J,
    "autocm": autocm,
    "amu_to_kg": amu_to_kg,
    "amu_to_au": amu_to_au,
    "bohr_m": bohr_m,
    "E_MECP": E_MECP,
    "E_MECP_cm1": E_MECP_cm1,
    "E_bins_cm1": E_bins_cm1,
    "E_LZ_cm1": E_LZ_cm1,
    "Estep": Estep,
    "maxn_py": maxn_py,
    "binX": binX,
    "DeltaF_parallel": DeltaF_parallel,
    "gradmean": gradmean,
    "mu_kg": mu_kg,
    "mu_au": mu_au,
    "velocity_floor": velocity_floor,

    "P_LZ_effective": P_LZ_effective,
    "gamma_LZ_effective": gamma_LZ_effective,
    "P_LZ_micro": P_LZ_micro,
    "gamma_LZ": gamma_LZ,
    "P_WC_effective": P_WC_effective,
    "P_WC_micro": P_WC_micro,

    # Step 9-compatible effective probability names.
    "probLZ_eff": P_LZ_effective,
    "gamma_LZ_eff_rate": gamma_LZ_effective,
    "probWC_eff": P_WC_effective,
    "airy_arg_WC_rate": airy_arg_WC_effective,
    "Ai_WC_rate": Ai_WC_effective,
    "v_bohr_s_rate": v_bohr_s,

    "airy_arg_WC_effective": airy_arg_WC_effective,
    "Ai_WC_effective": Ai_WC_effective,

    "P_LZ_intermediate": P_LZ_intermediate,
    "P_WC_intermediate": P_WC_intermediate,

    "P_LZ_channels": P_LZ_channels,
    "P_LZ_channel_groups": P_LZ_channel_groups,
    "P_WC_channels": P_WC_channels,
    "P_WC_channel_groups": P_WC_channel_groups,

    "SOC_channel_matrix_cm1": SOC_channel_matrix_cm1,
    "SOC_channel_matrix_label": SOC_channel_matrix_label,

    "v_bohr_s": v_bohr_s,
    "v_m_s": v_m_s,
    "compute_LZ_for_HSO_cm": compute_LZ_for_HSO_cm,
    "compute_WC_for_HSO_cm": compute_WC_for_HSO_cm,
    "spin_symbol": spin_symbol,
    "abs_ms_label": abs_ms_label,
    "ms_tex_value": ms_tex_value,
    "channel_tex_label": channel_tex_label,
    "make_group_channel_legend": make_group_channel_legend,
    "find_matching_hso_group": find_matching_hso_group,
    "MS_CHANNEL_HSO_GROUP_ATOL_CM1": hso_group_atol_cm1 if SOC_channel_matrix_cm1 is not None else float(globals().get("MS_CHANNEL_HSO_GROUP_ATOL_CM1", 1.0e-8)),
    "MS_CHANNEL_HSO_GROUP_RTOL": hso_group_rtol if SOC_channel_matrix_cm1 is not None else float(globals().get("MS_CHANNEL_HSO_GROUP_RTOL", 1.0e-10)),

    "local_step8": local_step8,
    "remote_step8": remote_step8,
    "summary_file_step8": summary_file_step8,
    "effective_grid_file_step8": effective_grid_file_step8,
    "channel_summary_file_step8": channel_summary_file_step8,
    "intermediate_summary_file_step8": intermediate_summary_file_step8,
    "lz_plot_file_step8": lz_plot_file_step8,
    "wc_plot_file_step8": wc_plot_file_step8,
    "remote_summary_file_step8": remote_summary_file_step8,
    "remote_effective_grid_file_step8": remote_effective_grid_file_step8,
    "remote_channel_summary_file_step8": remote_channel_summary_file_step8,
    "remote_intermediate_summary_file_step8": remote_intermediate_summary_file_step8,
    "remote_lz_plot_file_step8": remote_lz_plot_file_step8,
    "remote_wc_plot_file_step8": remote_wc_plot_file_step8
})

section("STEP 8 SUMMARY")

print(f"H_SO_cm effective curve           : {float(H_SO_cm):.6f} cm^-1")
print(f"E_MECP_cm1 threshold              : {E_MECP_cm1:.6f} cm^-1")
print(f"Common NAST grid                  : {E_bins_cm1[0]:.3f} to {E_bins_cm1[-1]:.3f} cm^-1")
print(f"Common NAST spacing               : {Estep:.3f} cm^-1")
print("Velocity source                   : Step 7; verified against Step 9 definition")
print(f"P_LZ_effective range              : {np.nanmin(P_LZ_effective):.6e} to {np.nanmax(P_LZ_effective):.6e}")
print(f"P_WC_effective range              : {np.nanmin(P_WC_effective):.6e} to {np.nanmax(P_WC_effective):.6e}")
print(f"Number of intermediate curves     : {len(P_LZ_intermediate)}")
print(f"Number of individual SOC channels : {len(P_LZ_channels)}")
print(f"Number of unique plotted channels : {len(P_LZ_channel_groups)}")

print("\nSTEP 8 COMPLETED SUCCESSFULLY.\n")


#%% STEP 9. CLUSTER NAST-STYLE EFFECTIVE + INTERMEDIATE + MS-SPECIFIC LZ/WC RATES

import os
import posixpath
import numpy as np
import matplotlib.pyplot as plt

print(r'''
====================================================================
 STEP 9 | CLUSTER VERSION
 NAST-style effective, intermediate, and MS-specific rates
====================================================================

This step computes NAST-style microcanonical rate constants using the
effective, intermediate, and MS-specific probabilities from Step 8.

No new ORCA calculation is performed here.

The active MECP threshold is forced to the ZPE-corrected barrier from
Step 7 when VaG_MECP_ZPE_corrected_cm1 is available.

Rate outputs include:

  1. Effective scalar-SOC LZ/WC rates
  2. Intermediate LZ/WC rates, k^0, k^1, k^1/2, ...
  3. Symmetry-collapsed MS-specific LZ/WC rates, k^{Ms_LS,Ms_HS}

Every nonzero MS-specific channel is calculated independently.
Channels with identical |H_SO| values generate identical probabilities
and rates; only one representative curve is plotted for such channels.
No MS-specific probabilities, numbers of states, or rates are summed.
''')

# ============================================================
# Required variables
# ============================================================

required_vars_step9 = [
    "jobname",
    "freq_reactant_real_cm1",
    "freq_MECP_real_cm1",
    "VaG_MECP_cm1",
    "E_LZ_cm1",
    "H_SO_cm",
    "DELTAF_PARALLEL_EH_PER_BOHR",
    "reduced_mass_amu",
    "Reference_rot_constants_cm1",
    "MECP_rot_constants_cm1",
    "local_base",
    "remote_base"
]

for var in required_vars_step9:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Steps 1–8 first."
        )

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = "MECP_ONLY"

SOC_EFFECTIVE_ONLY = bool(globals().get("SOC_EFFECTIVE_ONLY", False))
SOC_MATRIX_CHANNELS_AVAILABLE = bool(globals().get("SOC_MATRIX_CHANNELS_AVAILABLE", True))

if SOC_EFFECTIVE_ONLY or not SOC_MATRIX_CHANNELS_AVAILABLE:
    print("\nEffective-only SOC mode active.")
    print("Only effective LZ/WC rates will be computed/plotted.")

# ============================================================
# Helpers
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def save_current_figure(path):
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.show()


def ms_tex_value(x):
    x = float(x)

    if abs(x - round(x)) < 1.0e-8:
        return str(int(round(x)))

    if abs(abs(x) - 0.5) < 1.0e-8:
        return r"\frac{1}{2}" if x > 0 else r"-\frac{1}{2}"

    if abs(abs(x) - 1.5) < 1.0e-8:
        return r"\frac{3}{2}" if x > 0 else r"-\frac{3}{2}"

    if abs(abs(x) - 2.5) < 1.0e-8:
        return r"\frac{5}{2}" if x > 0 else r"-\frac{5}{2}"

    if abs(abs(x) - 3.5) < 1.0e-8:
        return r"\frac{7}{2}" if x > 0 else r"-\frac{7}{2}"

    return f"{x:.1f}"


def make_rate_group_legend(prefix, group_data):
    """
    Build a legend for one representative MS-specific rate curve.

    Multiple symbols mean that the corresponding channels have identical
    |H_SO| values and therefore identical probability and rate curves.
    Commas indicate coincident symmetry-equivalent curves; no summation
    is implied.
    """
    symbols = group_data.get("channel_symbols", [])
    H_abs = float(group_data["H_abs_cm1"])

    if len(symbols) == 0:
        return f"{prefix} MS |H|={H_abs:.1f}"

    rate_symbols = [sym.replace("$P", "$k") for sym in symbols]

    if len(rate_symbols) <= 3:
        joined = ", ".join(rate_symbols)
        return f"{prefix} {joined}, |H|={H_abs:.1f}"

    joined = ", ".join(rate_symbols[:3]) + ", ..."
    return (
        f"{prefix} {joined}, |H|={H_abs:.1f}, "
        f"equivalent channels={len(rate_symbols)}"
    )


# ============================================================
# Energy check and active threshold
# ============================================================

section("STEP 9 ENERGY CHECK")

if "VaG_MECP_electronic_cm1" in globals():
    print(f"Electronic MECP barrier      : {VaG_MECP_electronic_cm1:.6f} cm^-1")

if "VaG_MECP_ZPE_corrected_cm1" in globals():
    VaG_MECP_cm1 = float(VaG_MECP_ZPE_corrected_cm1)
    E_MECP = VaG_MECP_cm1
    E_MECP_cm1 = VaG_MECP_cm1

    print(f"ZPE-corrected MECP barrier   : {VaG_MECP_ZPE_corrected_cm1:.6f} cm^-1")
    print(f"Forced Step 9 threshold      : {VaG_MECP_cm1:.6f} cm^-1")

else:
    E_MECP = float(VaG_MECP_cm1)
    E_MECP_cm1 = E_MECP

    print(f"Active MECP barrier          : {VaG_MECP_cm1:.6f} cm^-1")

# ============================================================
# Constants
# ============================================================

section("CONSTANTS AND GRID SETUP")

autocm = 219474.6313705
h_SI = 6.62607015e-34
c_SI = 2.99792458e8
Eh_to_J = 4.3597447222071e-18
planck_hsec = h_SI / Eh_to_J

amu_kg = 1.66053906660e-27
bohr_m = 5.29177210903e-11
c_cm_s = 2.99792458e10
kJmol_per_cm1 = 0.01196266

E_LZ_cm1 = np.asarray(E_LZ_cm1, dtype=float).reshape(-1)

if E_LZ_cm1.size == 0:
    raise RuntimeError("E_LZ_cm1 is empty. Run Step 7 first.")

Estep = 1.0
maxn_py = int(np.ceil(np.nanmax(E_LZ_cm1) / Estep))
E_bins_cm1 = np.arange(maxn_py + 1, dtype=float) * Estep

binX = int(np.ceil(E_MECP / Estep))
binZPE = 0

degenR = float(globals().get("degenR", 1.0))

print(f"RUN_MODE                         : {RUN_MODE}")
print(f"workflow_mode                    : {workflow_mode}")
print(f"Estep                            : {Estep:.3f} cm^-1")
print(f"Maximum bin energy               : {E_bins_cm1[-1]:.3f} cm^-1")
print(f"Number of bins                   : {len(E_bins_cm1)}")
print(f"E_MECP                           : {E_MECP:.6f} cm^-1")
print(f"binX                             : {binX}")
print(f"degenR                           : {degenR}")
print(f"Effective H_SO_cm                : {float(H_SO_cm):.6f} cm^-1")
print(f"reduced_mass_amu                 : {float(reduced_mass_amu):.8f}")

# ============================================================
# Clean frequencies and rotations
# ============================================================

section("FREQUENCY AND ROTATIONAL DATA")

freq_R = np.asarray(freq_reactant_real_cm1, dtype=float).reshape(-1)
freq_X = np.asarray(freq_MECP_real_cm1, dtype=float).reshape(-1)

freq_R = freq_R[np.isfinite(freq_R) & (freq_R > 0.0)]
freq_X = freq_X[np.isfinite(freq_X) & (freq_X > 0.0)]

rot_R_cm1 = np.asarray(Reference_rot_constants_cm1, dtype=float).reshape(-1)
rot_X_cm1 = np.asarray(MECP_rot_constants_cm1, dtype=float).reshape(-1)

rot_R_cm1 = rot_R_cm1[np.isfinite(rot_R_cm1) & (rot_R_cm1 > 0.0)]
rot_X_cm1 = rot_X_cm1[np.isfinite(rot_X_cm1) & (rot_X_cm1 > 0.0)]

if len(rot_R_cm1) != 3:
    raise RuntimeError("Reference_rot_constants_cm1 must contain 3 positive rotational constants.")

if len(rot_X_cm1) != 3:
    raise RuntimeError("MECP_rot_constants_cm1 must contain 3 positive rotational constants.")

if len(freq_R) == 0:
    raise RuntimeError("No positive reactant frequencies were found.")

if len(freq_X) == 0:
    raise RuntimeError("No positive MECP effective-Hessian frequencies were found.")


def rot_constants_to_inertia_amu_bohr2(B_cm1):
    conv = h_SI / (8.0 * np.pi**2 * c_cm_s) / (amu_kg * bohr_m**2)
    return conv / np.asarray(B_cm1, dtype=float)


inertR = rot_constants_to_inertia_amu_bohr2(rot_R_cm1)
inertX = rot_constants_to_inertia_amu_bohr2(rot_X_cm1)

print(f"Reactant frequencies used         : {len(freq_R)}")
print(f"MECP frequencies used             : {len(freq_X)}")
print(f"Reference rot constants cm^-1     : {rot_R_cm1}")
print(f"MECP rot constants cm^-1          : {rot_X_cm1}")
print(f"Reference inertias amu Bohr^2     : {inertR}")
print(f"MECP inertias amu Bohr^2          : {inertX}")

# ============================================================
# NAST DOS helpers
# ============================================================

section("NAST ROVIBRATIONAL DENSITY OF STATES")

def nast_vib_dos(freqs_cm1, maxn, Estep):
    vib = np.zeros(maxn + 1, dtype=float)

    for freq in freqs_cm1:
        k = 1

        while (freq * k / Estep) <= maxn:
            q = int(np.ceil(freq * k / Estep))

            if q != 0 and q <= maxn:
                vib[q] += 1.0

            k += 1

    if maxn >= 1:
        vib[1] += 1.0

    vib = vib / Estep
    return vib


def nast_rot_dos(inert, maxn, Estep, autocm):
    rot = np.zeros(maxn + 1, dtype=float)
    Iprod = float(np.prod(inert))

    for i in range(1, maxn + 1):
        rot[i] = (
            4.0
            * np.sqrt(2.0 * float(i) * Estep / autocm * Iprod)
            / autocm
        )

    return rot


vibR = nast_vib_dos(freq_R, maxn_py, Estep)
vibTP = nast_vib_dos(freq_X, maxn_py, Estep)

vibX = np.zeros(maxn_py + 1, dtype=float)

if binX + 1 <= maxn_py:
    vibX[binX + 1:maxn_py + 1] = vibTP[1:maxn_py - binX + 1]

rotR = nast_rot_dos(inertR, maxn_py, Estep, autocm)
rotTP = nast_rot_dos(inertX, maxn_py, Estep, autocm)

rotX = np.zeros(maxn_py + 1, dtype=float)

for i in range(1, maxn_py - binX + 1):
    rotX[i + binX] = (
        4.0
        * np.sqrt(
            2.0 * float(i) * Estep / autocm * np.prod(inertX)
        )
        / autocm
    )

dosR = np.zeros(maxn_py + 1, dtype=float)
dosTP = np.zeros(maxn_py + 1, dtype=float)
dosX = np.zeros(maxn_py + 1, dtype=float)

for i in range(1, maxn_py - binZPE + 1):
    total = 0.0

    for j in range(1, i + 1):
        total += rotR[j] * vibR[i - j + 1] * Estep

    dosR[i] = total

for i in range(1, maxn_py + 1):
    total = 0.0

    for j in range(1, i + 1):
        total += rotTP[j] * vibTP[i - j + 1] * Estep

    dosTP[i] = total

if binX + 1 <= maxn_py:
    dosX[binX + 1:maxn_py + 1] = dosTP[1:maxn_py - binX + 1]

print(f"Reactant DOS max                 : {np.nanmax(dosR):.6e}")
print(f"MECP internal DOS max            : {np.nanmax(dosTP):.6e}")
print(f"MECP shifted LZ DOS max          : {np.nanmax(dosX):.6e}")

# ============================================================
# Probability helper for arbitrary HSO
# ============================================================

def compute_LZ_probability_for_HSO(H_cm, E_grid_cm1):
    E_grid_cm1 = np.asarray(E_grid_cm1, dtype=float).reshape(-1)

    H_cm = float(abs(H_cm))
    DeltaF_local = abs(float(DELTAF_PARALLEL_EH_PER_BOHR))
    mu_kg_local = float(reduced_mass_amu) * amu_kg

    H_J = h_SI * c_SI * H_cm * 100.0
    E_excess_J = (
        (E_grid_cm1 - E_MECP)
        * 100.0
        * h_SI
        * c_SI
    )

    v_m_s_local = np.zeros_like(E_grid_cm1)
    mask_energy = E_excess_J > 0.0

    v_m_s_local[mask_energy] = np.sqrt(
        2.0 * E_excess_J[mask_energy] / mu_kg_local
    )

    v_bohr_s_local = v_m_s_local / bohr_m

    gamma = np.full_like(E_grid_cm1, np.nan, dtype=float)
    P = np.zeros_like(E_grid_cm1, dtype=float)

    mask = (
        np.isfinite(E_grid_cm1)
        & np.isfinite(v_bohr_s_local)
        & (E_grid_cm1 >= E_MECP)
        & (v_bohr_s_local > 1.0e-12)
    )

    dDeltaE_dt_Eh_s = DeltaF_local * v_bohr_s_local
    dDeltaE_dt_J_s = dDeltaE_dt_Eh_s * Eh_to_J

    gamma[mask] = (
        4.0 * np.pi**2 * H_J**2
        / (h_SI * dDeltaE_dt_J_s[mask])
    )

    P[mask] = 1.0 - np.exp(-2.0 * gamma[mask])
    P = np.clip(P, 0.0, 1.0)

    return P, gamma, v_bohr_s_local

# ============================================================
# Rate helpers
# ============================================================

section("RATE HELPER FUNCTIONS")

def compute_rate_from_probability_LZ(prob_array):
    prob_array = np.asarray(prob_array, dtype=float).reshape(-1)

    if prob_array.size != maxn_py + 1:
        raise RuntimeError(
            f"LZ probability array has length {prob_array.size}, "
            f"expected {maxn_py + 1}."
        )

    nos = np.zeros(maxn_py + 1, dtype=float)
    rate = np.zeros(maxn_py + 1, dtype=float)

    if maxn_py > binX:
        for i in range(binX + 1, maxn_py + 1):
            total = 0.0

            for j in range(binX + 1, i + 1):
                idxX = i - j + 1 + binX

                if 1 <= idxX <= maxn_py:
                    total += prob_array[j] * dosX[idxX] * Estep

            nos[i] = total / autocm

    for i in range(1, maxn_py - binZPE + 1):
        if dosR[i] != 0.0:
            rate[i] = degenR * nos[i + binZPE] / (
                dosR[i] * planck_hsec
            )

    rate[~np.isfinite(rate)] = 0.0
    rate = np.clip(rate, 0.0, None)

    return nos, rate


def compute_rate_from_probability_WC(prob_array):
    prob_array = np.asarray(prob_array, dtype=float).reshape(-1)

    if prob_array.size != maxn_py + 1:
        raise RuntimeError(
            f"WC probability array has length {prob_array.size}, "
            f"expected {maxn_py + 1}."
        )

    nos = np.zeros(maxn_py + 1, dtype=float)
    rate = np.zeros(maxn_py + 1, dtype=float)

    for i in range(1, maxn_py + 1):
        total = 0.0

        for j in range(1, i + 1):
            idxX = i - j + 1

            if 1 <= idxX <= maxn_py:
                total += prob_array[j] * dosTP[idxX] * Estep

        nos[i] = total / autocm

    for i in range(1, maxn_py - binZPE + 1):
        if dosR[i] != 0.0:
            rate[i] = degenR * nos[i + binZPE] / (
                dosR[i] * planck_hsec
            )

    rate[~np.isfinite(rate)] = 0.0
    rate = np.clip(rate, 0.0, None)

    return nos, rate


compute_rate_from_probability = compute_rate_from_probability_LZ

print("Rate helper functions prepared.")

# ============================================================
# Effective LZ/WC rates
# ============================================================

section("EFFECTIVE LZ/WC RATE CALCULATION")

probLZ_eff, gamma_LZ_eff_rate, v_bohr_s_rate = (
    compute_LZ_probability_for_HSO(H_SO_cm, E_bins_cm1)
)

nosLZ_eff, rateLZ_eff = compute_rate_from_probability_LZ(probLZ_eff)

probLZ = probLZ_eff
gamma_LZ_rate = gamma_LZ_eff_rate
nosLZ = nosLZ_eff
rateLZ = rateLZ_eff

if "compute_WC_for_HSO_cm" in globals():
    probWC_eff, airy_arg_WC_rate, Ai_WC_rate = compute_WC_for_HSO_cm(
        H_SO_cm,
        E_bins_cm1
    )

elif "P_WC_effective" in globals():
    probWC_eff = np.asarray(P_WC_effective, dtype=float).reshape(-1)

    if probWC_eff.size != E_bins_cm1.size:
        raise RuntimeError(
            "P_WC_effective grid size differs from E_bins_cm1 and "
            "compute_WC_for_HSO_cm is unavailable."
        )

    airy_arg_WC_rate = np.full_like(E_bins_cm1, np.nan, dtype=float)
    Ai_WC_rate = np.full_like(E_bins_cm1, np.nan, dtype=float)

else:
    raise RuntimeError("WC probability is missing. Run Step 8 first.")

nosWC_eff, rateWC_eff = compute_rate_from_probability_WC(probWC_eff)

probWC = probWC_eff
nosWC = nosWC_eff
rateWC = rateWC_eff

print(f"P_LZ effective range              : {np.nanmin(probLZ_eff):.6e} to {np.nanmax(probLZ_eff):.6e}")
print(f"k_LZ effective range              : {np.nanmin(rateLZ_eff):.6e} to {np.nanmax(rateLZ_eff):.6e}")
print(f"P_WC effective range              : {np.nanmin(probWC_eff):.6e} to {np.nanmax(probWC_eff):.6e}")
print(f"k_WC effective range              : {np.nanmin(rateWC_eff):.6e} to {np.nanmax(rateWC_eff):.6e}")

# ============================================================
# Intermediate rates from Step 8
# ============================================================

section("INTERMEDIATE RATE CALCULATION")

k_LZ_intermediate = {}
N_LZ_intermediate = {}
P_LZ_intermediate_rate_grid = {}

if "P_LZ_intermediate" in globals() and len(P_LZ_intermediate) > 0:
    for int_label, int_data in P_LZ_intermediate.items():
        H_int = float(int_data["H_int_cm1"])

        P_int, gamma_int, _ = compute_LZ_probability_for_HSO(
            H_int,
            E_bins_cm1
        )

        N_int, k_int = compute_rate_from_probability_LZ(P_int)

        P_LZ_intermediate_rate_grid[int_label] = P_int
        N_LZ_intermediate[int_label] = N_int
        k_LZ_intermediate[int_label] = {
            "H_int_cm1": H_int,
            "abs_Ms_low": float(int_data["abs_Ms_low"]),
            "degeneracy": int(int_data["degeneracy"]),
            "k": k_int,
            "N": N_int,
            "P": P_int
        }

    print(f"Computed LZ intermediate rates for {len(k_LZ_intermediate)} curves.")
else:
    print("No P_LZ_intermediate found from Step 8.")

k_WC_intermediate = {}
N_WC_intermediate = {}
P_WC_intermediate_rate_grid = {}

if "P_WC_intermediate" in globals() and len(P_WC_intermediate) > 0:
    for int_label, int_data in P_WC_intermediate.items():
        H_int = float(int_data["H_int_cm1"])

        if "compute_WC_for_HSO_cm" in globals():
            P_int, _, _ = compute_WC_for_HSO_cm(H_int, E_bins_cm1)
        else:
            P_int = np.asarray(int_data["P"], dtype=float).reshape(-1)

            if P_int.size != E_bins_cm1.size:
                raise RuntimeError(
                    f"WC intermediate {int_label} grid size differs from E_bins_cm1."
                )

        N_int, k_int = compute_rate_from_probability_WC(P_int)

        P_WC_intermediate_rate_grid[int_label] = P_int
        N_WC_intermediate[int_label] = N_int
        k_WC_intermediate[int_label] = {
            "H_int_cm1": H_int,
            "abs_Ms_low": float(int_data["abs_Ms_low"]),
            "degeneracy": int(int_data["degeneracy"]),
            "k": k_int,
            "N": N_int,
            "P": P_int
        }

    print(f"Computed WC intermediate rates for {len(k_WC_intermediate)} curves.")
else:
    print("No P_WC_intermediate found from Step 8.")

# ============================================================
# MS-specific channel rates
# ============================================================

section("MS-SPECIFIC CHANNEL RATE CALCULATION")

k_LZ_channels = {}
N_LZ_channels = {}
P_LZ_channels_rate_grid = {}

if "P_LZ_channels" in globals() and len(P_LZ_channels) > 0:
    for ch_label, ch_data in P_LZ_channels.items():
        H_abs = float(ch_data["H_abs_cm1"])

        P_ch, gamma_ch, _ = compute_LZ_probability_for_HSO(
            H_abs,
            E_bins_cm1
        )

        N_ch, k_ch = compute_rate_from_probability_LZ(P_ch)

        P_LZ_channels_rate_grid[ch_label] = P_ch
        N_LZ_channels[ch_label] = N_ch
        k_LZ_channels[ch_label] = k_ch

    print(f"Computed LZ rates for {len(k_LZ_channels)} individual MS-specific channels.")
else:
    print("No P_LZ_channels found from Step 8.")

k_WC_channels = {}
N_WC_channels = {}
P_WC_channels_rate_grid = {}

if "P_WC_channels" in globals() and len(P_WC_channels) > 0:
    for ch_label, ch_data in P_WC_channels.items():
        H_abs = float(ch_data["H_abs_cm1"])

        if "compute_WC_for_HSO_cm" in globals():
            P_ch, _, _ = compute_WC_for_HSO_cm(H_abs, E_bins_cm1)
        else:
            P_ch = np.asarray(ch_data["P"], dtype=float).reshape(-1)

            if P_ch.size != E_bins_cm1.size:
                raise RuntimeError(
                    f"WC channel {ch_label} probability grid size differs from E_bins_cm1."
                )

        N_ch, k_ch = compute_rate_from_probability_WC(P_ch)

        P_WC_channels_rate_grid[ch_label] = P_ch
        N_WC_channels[ch_label] = N_ch
        k_WC_channels[ch_label] = k_ch

    print(f"Computed WC rates for {len(k_WC_channels)} individual MS-specific channels.")
else:
    print("No P_WC_channels found from Step 8.")

# ============================================================
# Symmetry-collapsed MS-specific channel rates
# ============================================================

section("SYMMETRY-COLLAPSED MS-SPECIFIC RATE CURVES")

# Step 8 groups channels only when their |H_SO| values are numerically
# identical. Such channels have identical probabilities and therefore
# identical N(E) and k(E). We retain one representative curve for each
# group and keep all channel labels for the legend. Nothing is summed.

k_LZ_channel_groups = {}
N_LZ_channel_groups = {}
P_LZ_channel_groups_rate_grid = {}

if "P_LZ_channel_groups" in globals() and len(P_LZ_channel_groups) > 0:
    for group_key, group_data in P_LZ_channel_groups.items():
        labels = list(group_data.get("labels", []))

        representative_label = group_data.get("representative_label")
        if representative_label not in k_LZ_channels:
            representative_label = next(
                (label for label in labels if label in k_LZ_channels),
                None
            )

        if representative_label is None:
            continue

        k_rep = np.asarray(
            k_LZ_channels[representative_label],
            dtype=float
        ).copy()

        N_rep = np.asarray(
            N_LZ_channels[representative_label],
            dtype=float
        ).copy()

        P_rep = np.asarray(
            P_LZ_channels_rate_grid[representative_label],
            dtype=float
        ).copy()

        k_LZ_channel_groups[group_key] = {
            "H_abs_cm1": float(group_data["H_abs_cm1"]),
            "labels": labels,
            "channel_symbols": list(
                group_data.get("channel_symbols", [])
            ),
            "degeneracy": len(labels),
            "representative_label": representative_label,
            "k_representative": k_rep,
            "N_representative": N_rep,
            "P_representative": P_rep,
            "combination_rule": "representative_only_no_sum"
        }

        N_LZ_channel_groups[group_key] = N_rep
        P_LZ_channel_groups_rate_grid[group_key] = P_rep


k_WC_channel_groups = {}
N_WC_channel_groups = {}
P_WC_channel_groups_rate_grid = {}

if "P_WC_channel_groups" in globals() and len(P_WC_channel_groups) > 0:
    for group_key, group_data in P_WC_channel_groups.items():
        labels = list(group_data.get("labels", []))

        representative_label = group_data.get("representative_label")
        if representative_label not in k_WC_channels:
            representative_label = next(
                (label for label in labels if label in k_WC_channels),
                None
            )

        if representative_label is None:
            continue

        k_rep = np.asarray(
            k_WC_channels[representative_label],
            dtype=float
        ).copy()

        N_rep = np.asarray(
            N_WC_channels[representative_label],
            dtype=float
        ).copy()

        P_rep = np.asarray(
            P_WC_channels_rate_grid[representative_label],
            dtype=float
        ).copy()

        k_WC_channel_groups[group_key] = {
            "H_abs_cm1": float(group_data["H_abs_cm1"]),
            "labels": labels,
            "channel_symbols": list(
                group_data.get("channel_symbols", [])
            ),
            "degeneracy": len(labels),
            "representative_label": representative_label,
            "k_representative": k_rep,
            "N_representative": N_rep,
            "P_representative": P_rep,
            "combination_rule": "representative_only_no_sum"
        }

        N_WC_channel_groups[group_key] = N_rep
        P_WC_channel_groups_rate_grid[group_key] = P_rep

print(f"Representative LZ MS curves       : {len(k_LZ_channel_groups)}")
print(f"Representative WC MS curves       : {len(k_WC_channel_groups)}")
print("MS-specific combination rule      : representative only; no summation")

# ============================================================
# Final arrays and backward-compatible names
# ============================================================

section("FINAL RATE ARRAYS")

E_rate_LZ_cm1 = E_bins_cm1[1:]
E_rate_WC_cm1 = E_bins_cm1[1:]

rho_reactant_cm = dosR[1:]
rho_MECP_internal_cm = dosTP[1:]
rho_MECP_shifted_plot = dosX[1:]

P_LZ_rate_grid = probLZ_eff[1:]
gamma_LZ_rate_grid = gamma_LZ_eff_rate[1:]
v_bohr_s_rate_grid = v_bohr_s_rate[1:]

N_MECP_LZ = nosLZ_eff[1:]
k_LZ_micro_s_inv = rateLZ_eff[1:]

k_LZ_effective_micro_s_inv = k_LZ_micro_s_inv
N_MECP_LZ_effective = N_MECP_LZ

positive_k = k_LZ_micro_s_inv > 0.0
log_k_LZ_raw = np.full_like(k_LZ_micro_s_inv, np.nan)
log_k_LZ_raw[positive_k] = np.log10(k_LZ_micro_s_inv[positive_k])
E_LZ_log_raw_cm1 = E_rate_LZ_cm1

P_WC_rate_grid = probWC_eff[1:]
N_MECP_WC = nosWC_eff[1:]
k_WC_micro_s_inv = rateWC_eff[1:]

k_WC_effective_micro_s_inv = k_WC_micro_s_inv
N_MECP_WC_effective = N_MECP_WC

positive_k_wc = k_WC_micro_s_inv > 0.0
log_k_WC_raw = np.full_like(k_WC_micro_s_inv, np.nan)
log_k_WC_raw[positive_k_wc] = np.log10(k_WC_micro_s_inv[positive_k_wc])
E_WC_log_raw_cm1 = E_rate_WC_cm1

dosR_NAST = dosR
dosX_NAST = dosX
dosTP_NAST = dosTP

nosLZ_NAST = nosLZ_eff
rateLZ_NAST = rateLZ_eff

nosWC_NAST = nosWC_eff
rateWC_NAST = rateWC_eff

print(f"E_rate_LZ_cm1 range               : {E_rate_LZ_cm1[0]:.3f} to {E_rate_LZ_cm1[-1]:.3f}")
print(f"E_rate_WC_cm1 range               : {E_rate_WC_cm1[0]:.3f} to {E_rate_WC_cm1[-1]:.3f}")
print(f"k_LZ effective final max           : {np.nanmax(k_LZ_micro_s_inv):.6e} s^-1")
print(f"k_WC effective final max           : {np.nanmax(k_WC_micro_s_inv):.6e} s^-1")

# ============================================================
# Save plots
# ============================================================

section("PLOTTING STEP 9 RATE RESULTS")

local_step9 = os.path.join(local_base, "NAST_rates")
os.makedirs(local_step9, exist_ok=True)

remote_step9 = posixpath.join(remote_base, "NAST_rates")

if "remote_mkdir_p" in globals() and "sftp" in globals():
    try:
        remote_mkdir_p(sftp, remote_step9)
    except Exception:
        pass

plot_files_step9 = {}

def register_plot(name):
    path = os.path.join(local_step9, f"{jobname}_Step9_{name}.png")
    plot_files_step9[name] = path
    return path

# LZ probability used in rate
plt.figure(figsize=(8, 4.5))
plt.plot(
    E_rate_LZ_cm1,
    P_LZ_rate_grid,
    linewidth=2,
    label=f"LZ $P^{{eff}}$, HSO={H_SO_cm:.1f}"
)
plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("Landau-Zener probability")
plt.title("Effective LZ Probability Used in Rate")
plt.grid(False)
plt.legend()
save_current_figure(register_plot("LZ_probability_used_in_rate"))

# DOS
plt.figure(figsize=(8, 4.5))
plt.plot(E_rate_LZ_cm1, rho_reactant_cm, linewidth=2, label="Reactant rovib DOS")
plt.plot(E_rate_LZ_cm1, rho_MECP_shifted_plot, linewidth=2, label="MECP rovib DOS shifted for LZ")
plt.plot(E_rate_LZ_cm1, rho_MECP_internal_cm, linewidth=2, linestyle=":", label="MECP internal rovib DOS for WC")
plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("NAST DOS")
plt.title("NAST-style Rovibrational DOS")
plt.grid(False)
plt.legend()
save_current_figure(register_plot("rovibrational_DOS"))

# Number of states
plt.figure(figsize=(8, 4.5))
plt.plot(E_rate_LZ_cm1, N_MECP_LZ, linewidth=2, label="LZ N(E)")
plt.plot(E_rate_WC_cm1, N_MECP_WC, linewidth=2, linestyle=":", label="WC N(E)")
plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("N(E)")
plt.title("Effective Number of States: LZ vs WC")
plt.grid(False)
plt.legend()
save_current_figure(register_plot("number_of_states_LZ_vs_WC"))

# LZ rates linear
plt.figure(figsize=(9.2, 5.2))
plt.plot(
    E_rate_LZ_cm1,
    k_LZ_effective_micro_s_inv,
    linewidth=2.2,
    color="k",
    linestyle=":",
    label="LZ $k^{eff}$"
)

for key, data in sorted(
    k_LZ_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    plt.plot(
        E_rate_LZ_cm1,
        data["k"][1:],
        linewidth=2.0,
        linestyle="--",
        label=(
            f"LZ $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_LZ_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    plt.plot(
        E_rate_LZ_cm1,
        data["k_representative"][1:],
        linewidth=1.5,
        label=make_rate_group_legend("LZ", data)
    )

plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel(r"$k_{\mathrm{LZ}}(E)$ (s$^{-1}$)")
plt.title("Effective, Intermediate, and MS-specific LZ Microcanonical Rates")
plt.grid(False)
plt.legend(fontsize=7)
save_current_figure(register_plot("LZ_effective_intermediate_MS_rates"))

# LZ log rates
plt.figure(figsize=(9.2, 5.2))
mask_eff_lz = k_LZ_effective_micro_s_inv > 0.0
plt.plot(
    E_rate_LZ_cm1[mask_eff_lz],
    np.log10(k_LZ_effective_micro_s_inv[mask_eff_lz]),
    linewidth=2.2,
    color="k",
    linestyle=":",
    label="LZ $k^{eff}$"
)

for key, data in sorted(
    k_LZ_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    k_int = data["k"][1:]
    mask = k_int > 0.0

    plt.plot(
        E_rate_LZ_cm1[mask],
        np.log10(k_int[mask]),
        linewidth=2.0,
        linestyle="--",
        label=(
            f"LZ $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_LZ_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    k_group = data["k_representative"][1:]
    mask = k_group > 0.0

    plt.plot(
        E_rate_LZ_cm1[mask],
        np.log10(k_group[mask]),
        linewidth=1.5,
        label=make_rate_group_legend("LZ", data)
    )

plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel(r"$\log_{10}[k_{\mathrm{LZ}}(E)]$")
plt.title("Effective, Intermediate, and MS-specific LZ Log Rates")
plt.grid(False)
plt.legend(fontsize=7)
save_current_figure(register_plot("LZ_effective_intermediate_MS_log_rates"))

# WC probability used in rate
plt.figure(figsize=(8, 4.5))
plt.plot(
    E_rate_WC_cm1,
    P_WC_rate_grid,
    linewidth=2,
    label=f"WC $P^{{eff}}$, HSO={H_SO_cm:.1f}"
)
plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel("Weak-coupling probability")
plt.title("Effective WC Probability Used in Rate")
plt.grid(False)
plt.legend()
save_current_figure(register_plot("WC_probability_used_in_rate"))

# WC rates linear
plt.figure(figsize=(9.2, 5.2))
plt.plot(
    E_rate_WC_cm1,
    k_WC_effective_micro_s_inv,
    linewidth=2.2,
    color="k",
    linestyle=":",
    label="WC $k^{eff}$"
)

for key, data in sorted(
    k_WC_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    plt.plot(
        E_rate_WC_cm1,
        data["k"][1:],
        linewidth=2.0,
        linestyle="--",
        label=(
            f"WC $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_WC_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    plt.plot(
        E_rate_WC_cm1,
        data["k_representative"][1:],
        linewidth=1.5,
        label=make_rate_group_legend("WC", data)
    )

plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel(r"$k_{\mathrm{WC}}(E)$ (s$^{-1}$)")
plt.title("Effective, Intermediate, and MS-specific WC Microcanonical Rates")
plt.grid(False)
plt.legend(fontsize=7)
save_current_figure(register_plot("WC_effective_intermediate_MS_rates"))

# WC log rates
plt.figure(figsize=(9.2, 5.2))
mask_eff_wc = k_WC_effective_micro_s_inv > 0.0
plt.plot(
    E_rate_WC_cm1[mask_eff_wc],
    np.log10(k_WC_effective_micro_s_inv[mask_eff_wc]),
    linewidth=2.2,
    color="k",
    linestyle=":",
    label="WC $k^{eff}$"
)

for key, data in sorted(
    k_WC_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    k_int = data["k"][1:]
    mask = k_int > 0.0

    plt.plot(
        E_rate_WC_cm1[mask],
        np.log10(k_int[mask]),
        linewidth=2.0,
        linestyle="--",
        label=(
            f"WC $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_WC_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    k_group = data["k_representative"][1:]
    mask = k_group > 0.0

    plt.plot(
        E_rate_WC_cm1[mask],
        np.log10(k_group[mask]),
        linewidth=1.5,
        label=make_rate_group_legend("WC", data)
    )

plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.5, label="ZPE-corrected MECP")
plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
plt.ylabel(r"$\log_{10}[k_{\mathrm{WC}}(E)]$")
plt.title("Effective, Intermediate, and MS-specific WC Log Rates")
plt.grid(False)
plt.legend(fontsize=7)
save_current_figure(register_plot("WC_effective_intermediate_MS_log_rates"))

# ============================================================
# Save numerical outputs
# ============================================================

section("SAVING STEP 9 OUTPUTS")

summary_file_step9 = os.path.join(
    local_step9,
    f"{jobname}_Step9_rate_summary.txt"
)

rate_grid_file_step9 = os.path.join(
    local_step9,
    f"{jobname}_Step9_effective_rate_grid.txt"
)

channel_rate_summary_file_step9 = os.path.join(
    local_step9,
    f"{jobname}_Step9_channel_rate_summary.txt"
)

intermediate_rate_summary_file_step9 = os.path.join(
    local_step9,
    f"{jobname}_Step9_intermediate_rate_summary.txt"
)

dos_file_step9 = os.path.join(
    local_step9,
    f"{jobname}_Step9_DOS_grid.txt"
)

summary_lines = []
summary_lines.append("Step 9 NAST-style effective, intermediate, and MS-specific LZ/WC rates")
summary_lines.append(f"Run mode = {RUN_MODE}")
summary_lines.append(f"Workflow mode = {workflow_mode}")
summary_lines.append("")
summary_lines.append("[ENERGY]")
summary_lines.append(f"E_MECP_cm1 = {E_MECP_cm1:.12f}")
summary_lines.append(f"E_MECP_kJmol = {E_MECP_cm1 * kJmol_per_cm1:.12f}")
summary_lines.append(f"Estep = {Estep:.12f}")
summary_lines.append(f"maxn_py = {maxn_py}")
summary_lines.append(f"binX = {binX}")
summary_lines.append(f"binZPE = {binZPE}")
summary_lines.append("")
summary_lines.append("[INPUTS]")
summary_lines.append(f"H_SO_cm = {float(H_SO_cm):.12f}")
summary_lines.append(f"DELTAF_PARALLEL_EH_PER_BOHR = {float(DELTAF_PARALLEL_EH_PER_BOHR):.12e}")
summary_lines.append(f"reduced_mass_amu = {float(reduced_mass_amu):.12f}")
summary_lines.append(f"degenR = {degenR:.12f}")
summary_lines.append(f"Reactant_frequency_count = {len(freq_R)}")
summary_lines.append(f"MECP_frequency_count = {len(freq_X)}")
summary_lines.append("")
summary_lines.append("[EFFECTIVE_RANGES]")
summary_lines.append(f"P_LZ_min = {np.nanmin(P_LZ_rate_grid):.12e}")
summary_lines.append(f"P_LZ_max = {np.nanmax(P_LZ_rate_grid):.12e}")
summary_lines.append(f"k_LZ_min = {np.nanmin(k_LZ_micro_s_inv):.12e}")
summary_lines.append(f"k_LZ_max = {np.nanmax(k_LZ_micro_s_inv):.12e}")
summary_lines.append(f"P_WC_min = {np.nanmin(P_WC_rate_grid):.12e}")
summary_lines.append(f"P_WC_max = {np.nanmax(P_WC_rate_grid):.12e}")
summary_lines.append(f"k_WC_min = {np.nanmin(k_WC_micro_s_inv):.12e}")
summary_lines.append(f"k_WC_max = {np.nanmax(k_WC_micro_s_inv):.12e}")
summary_lines.append("")
summary_lines.append("[INTERMEDIATE]")
summary_lines.append(f"LZ_intermediate_curves = {len(k_LZ_intermediate)}")
summary_lines.append(f"WC_intermediate_curves = {len(k_WC_intermediate)}")
summary_lines.append("")
summary_lines.append("[MS_SPECIFIC_CHANNELS]")
summary_lines.append(f"LZ_individual_channels = {len(k_LZ_channels)}")
summary_lines.append(f"WC_individual_channels = {len(k_WC_channels)}")
summary_lines.append(f"LZ_representative_channel_groups = {len(k_LZ_channel_groups)}")
summary_lines.append(f"WC_representative_channel_groups = {len(k_WC_channel_groups)}")
summary_lines.append("")
summary_lines.append("[FILES]")
summary_lines.append(f"rate_grid_file_step9 = {rate_grid_file_step9}")
summary_lines.append(f"intermediate_rate_summary_file_step9 = {intermediate_rate_summary_file_step9}")
summary_lines.append(f"channel_rate_summary_file_step9 = {channel_rate_summary_file_step9}")
summary_lines.append(f"dos_file_step9 = {dos_file_step9}")

for name, path in plot_files_step9.items():
    summary_lines.append(f"plot_{name} = {path}")

write_local_text(summary_file_step9, "\n".join(summary_lines) + "\n")

rate_grid_lines = []
rate_grid_lines.append(
    "E_cm1  "
    "P_LZ  gamma_LZ  N_LZ  k_LZ_s_inv  log10_k_LZ  "
    "P_WC  N_WC  k_WC_s_inv  log10_k_WC"
)

for i in range(len(E_rate_LZ_cm1)):
    rate_grid_lines.append(
        f"{E_rate_LZ_cm1[i]:18.10f} "
        f"{P_LZ_rate_grid[i]:18.10e} "
        f"{gamma_LZ_rate_grid[i]:18.10e} "
        f"{N_MECP_LZ[i]:18.10e} "
        f"{k_LZ_micro_s_inv[i]:18.10e} "
        f"{log_k_LZ_raw[i]:18.10e} "
        f"{P_WC_rate_grid[i]:18.10e} "
        f"{N_MECP_WC[i]:18.10e} "
        f"{k_WC_micro_s_inv[i]:18.10e} "
        f"{log_k_WC_raw[i]:18.10e}"
    )

write_local_text(rate_grid_file_step9, "\n".join(rate_grid_lines) + "\n")

dos_lines = []
dos_lines.append("E_cm1  rho_reactant_cm  rho_MECP_shifted_LZ  rho_MECP_internal_WC")

for E, rR, rX, rTP in zip(
    E_rate_LZ_cm1,
    rho_reactant_cm,
    rho_MECP_shifted_plot,
    rho_MECP_internal_cm
):
    dos_lines.append(
        f"{E:18.10f} "
        f"{rR:18.10e} "
        f"{rX:18.10e} "
        f"{rTP:18.10e}"
    )

write_local_text(dos_file_step9, "\n".join(dos_lines) + "\n")

intermediate_lines = []
intermediate_lines.append("Step 9 intermediate rate summary")
intermediate_lines.append("")
intermediate_lines.append("[LZ_INTERMEDIATE]")
intermediate_lines.append("label  abs_Ms_low  degeneracy  H_int_cm1  k_max_s_inv")

for key, data in sorted(
    k_LZ_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    intermediate_lines.append(
        f"{key:20s} "
        f"{data['abs_Ms_low']:12.6f} "
        f"{data['degeneracy']:5d} "
        f"{data['H_int_cm1']:18.10f} "
        f"{np.nanmax(data['k'][1:]):18.10e}"
    )

intermediate_lines.append("")
intermediate_lines.append("[WC_INTERMEDIATE]")
intermediate_lines.append("label  abs_Ms_low  degeneracy  H_int_cm1  k_max_s_inv")

for key, data in sorted(
    k_WC_intermediate.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    intermediate_lines.append(
        f"{key:20s} "
        f"{data['abs_Ms_low']:12.6f} "
        f"{data['degeneracy']:5d} "
        f"{data['H_int_cm1']:18.10f} "
        f"{np.nanmax(data['k'][1:]):18.10e}"
    )

write_local_text(intermediate_rate_summary_file_step9, "\n".join(intermediate_lines) + "\n")

channel_lines = []
channel_lines.append("Step 9 representative-group and individual MS-specific channel rate summary")
channel_lines.append("")
channel_lines.append("[REPRESENTATIVE_LZ_GROUPS_NO_SUM]")
channel_lines.append("group_key  H_abs_cm1  equivalent_count  symbols  representative_label  k_max_s_inv  labels")

for key, data in sorted(
    k_LZ_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    k_group = data["k_representative"][1:]
    channel_lines.append(
        f"{key:16s} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['degeneracy']:5d} "
        f"{', '.join(data.get('channel_symbols', [])):30s} "
        f"{data['representative_label']:50s} "
        f"{np.nanmax(k_group):18.10e} "
        + ",".join(data["labels"])
    )

channel_lines.append("")
channel_lines.append("[REPRESENTATIVE_WC_GROUPS_NO_SUM]")
channel_lines.append("group_key  H_abs_cm1  equivalent_count  symbols  representative_label  k_max_s_inv  labels")

for key, data in sorted(
    k_WC_channel_groups.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    k_group = data["k_representative"][1:]
    channel_lines.append(
        f"{key:16s} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['degeneracy']:5d} "
        f"{', '.join(data.get('channel_symbols', [])):30s} "
        f"{data['representative_label']:50s} "
        f"{np.nanmax(k_group):18.10e} "
        + ",".join(data["labels"])
    )

channel_lines.append("")
channel_lines.append("[INDIVIDUAL_LZ]")
channel_lines.append("label  k_max_s_inv")

for label, k_ch in sorted(
    k_LZ_channels.items(),
    key=lambda x: np.nanmax(x[1]),
    reverse=True
):
    channel_lines.append(
        f"{label:50s} {np.nanmax(k_ch[1:]):18.10e}"
    )

channel_lines.append("")
channel_lines.append("[INDIVIDUAL_WC]")
channel_lines.append("label  k_max_s_inv")

for label, k_ch in sorted(
    k_WC_channels.items(),
    key=lambda x: np.nanmax(x[1]),
    reverse=True
):
    channel_lines.append(
        f"{label:50s} {np.nanmax(k_ch[1:]):18.10e}"
    )

write_local_text(channel_rate_summary_file_step9, "\n".join(channel_lines) + "\n")

remote_summary_file_step9 = None
remote_rate_grid_file_step9 = None
remote_channel_rate_summary_file_step9 = None
remote_intermediate_rate_summary_file_step9 = None
remote_dos_file_step9 = None
remote_plot_files_step9 = {}

if "sftp" in globals():
    try:
        remote_summary_file_step9 = posixpath.join(
            remote_step9,
            posixpath.basename(summary_file_step9)
        )
        remote_rate_grid_file_step9 = posixpath.join(
            remote_step9,
            posixpath.basename(rate_grid_file_step9)
        )
        remote_channel_rate_summary_file_step9 = posixpath.join(
            remote_step9,
            posixpath.basename(channel_rate_summary_file_step9)
        )
        remote_intermediate_rate_summary_file_step9 = posixpath.join(
            remote_step9,
            posixpath.basename(intermediate_rate_summary_file_step9)
        )
        remote_dos_file_step9 = posixpath.join(
            remote_step9,
            posixpath.basename(dos_file_step9)
        )

        sftp.put(summary_file_step9, remote_summary_file_step9)
        sftp.put(rate_grid_file_step9, remote_rate_grid_file_step9)
        sftp.put(channel_rate_summary_file_step9, remote_channel_rate_summary_file_step9)
        sftp.put(intermediate_rate_summary_file_step9, remote_intermediate_rate_summary_file_step9)
        sftp.put(dos_file_step9, remote_dos_file_step9)

        for name, local_path in plot_files_step9.items():
            remote_path = posixpath.join(
                remote_step9,
                posixpath.basename(local_path)
            )
            sftp.put(local_path, remote_path)
            remote_plot_files_step9[name] = remote_path

    except Exception:
        remote_summary_file_step9 = None
        remote_rate_grid_file_step9 = None
        remote_channel_rate_summary_file_step9 = None
        remote_intermediate_rate_summary_file_step9 = None
        remote_dos_file_step9 = None
        remote_plot_files_step9 = {}

print(f"Local Step 9 summary              : {summary_file_step9}")
print(f"Local effective rate grid         : {rate_grid_file_step9}")
print(f"Local intermediate rate summary   : {intermediate_rate_summary_file_step9}")
print(f"Local channel rate summary        : {channel_rate_summary_file_step9}")
print(f"Local DOS grid                    : {dos_file_step9}")

for name, path in plot_files_step9.items():
    print(f"Local plot {name:40s}: {path}")

if remote_summary_file_step9:
    print(f"Remote Step 9 summary             : {remote_summary_file_step9}")
    print(f"Remote effective rate grid        : {remote_rate_grid_file_step9}")
    print(f"Remote intermediate rate summary  : {remote_intermediate_rate_summary_file_step9}")
    print(f"Remote channel rate summary       : {remote_channel_rate_summary_file_step9}")
    print(f"Remote DOS grid                   : {remote_dos_file_step9}")

# ============================================================
# High-energy diagnostic
# ============================================================

section("HIGH-ENERGY DIAGNOSTIC")

idx_hi = -1

print(f"E_total                          : {E_rate_LZ_cm1[idx_hi]:.3f} cm^-1")
print(f"P_LZ effective                    : {P_LZ_rate_grid[idx_hi]:.6e}")
print(f"P_WC effective                    : {P_WC_rate_grid[idx_hi]:.6e}")
print(f"dosR                              : {rho_reactant_cm[idx_hi]:.6e}")
print(f"dosX shifted LZ                   : {rho_MECP_shifted_plot[idx_hi]:.6e}")
print(f"dosTP internal WC                 : {rho_MECP_internal_cm[idx_hi]:.6e}")
print(f"N_LZ_eff                          : {N_MECP_LZ[idx_hi]:.6e}")
print(f"N_WC_eff                          : {N_MECP_WC[idx_hi]:.6e}")
print(f"k_LZ_eff                          : {k_LZ_micro_s_inv[idx_hi]:.6e} s^-1")
print(f"k_WC_eff                          : {k_WC_micro_s_inv[idx_hi]:.6e} s^-1")

if k_LZ_micro_s_inv[idx_hi] > 0.0:
    print(f"log10(k_LZ_eff)                   : {np.log10(k_LZ_micro_s_inv[idx_hi]):.6f}")

if k_WC_micro_s_inv[idx_hi] > 0.0:
    print(f"log10(k_WC_eff)                   : {np.log10(k_WC_micro_s_inv[idx_hi]):.6f}")

# ============================================================
# Export variables
# ============================================================

globals().update({
    "RUN_MODE": RUN_MODE,
    "workflow_mode": workflow_mode,
    "VaG_MECP_cm1": VaG_MECP_cm1,
    "E_MECP": E_MECP,
    "E_MECP_cm1": E_MECP_cm1,
    "autocm": autocm,
    "h_SI": h_SI,
    "c_SI": c_SI,
    "Eh_to_J": Eh_to_J,
    "planck_hsec": planck_hsec,
    "amu_kg": amu_kg,
    "bohr_m": bohr_m,
    "c_cm_s": c_cm_s,
    "kJmol_per_cm1": kJmol_per_cm1,
    "Estep": Estep,
    "maxn_py": maxn_py,
    "E_bins_cm1": E_bins_cm1,
    "binX": binX,
    "binZPE": binZPE,
    "degenR": degenR,
    "freq_R": freq_R,
    "freq_X": freq_X,
    "rot_R_cm1": rot_R_cm1,
    "rot_X_cm1": rot_X_cm1,
    "inertR": inertR,
    "inertX": inertX,
    "vibR": vibR,
    "vibTP": vibTP,
    "vibX": vibX,
    "rotR": rotR,
    "rotTP": rotTP,
    "rotX": rotX,
    "dosR": dosR,
    "dosTP": dosTP,
    "dosX": dosX,
    "dosR_NAST": dosR_NAST,
    "dosX_NAST": dosX_NAST,
    "dosTP_NAST": dosTP_NAST,
    "compute_LZ_probability_for_HSO": compute_LZ_probability_for_HSO,
    "compute_rate_from_probability_LZ": compute_rate_from_probability_LZ,
    "compute_rate_from_probability_WC": compute_rate_from_probability_WC,
    "compute_rate_from_probability": compute_rate_from_probability,
    "probLZ_eff": probLZ_eff,
    "gamma_LZ_eff_rate": gamma_LZ_eff_rate,
    "v_bohr_s_rate": v_bohr_s_rate,
    "nosLZ_eff": nosLZ_eff,
    "rateLZ_eff": rateLZ_eff,
    "probLZ": probLZ,
    "gamma_LZ_rate": gamma_LZ_rate,
    "nosLZ": nosLZ,
    "rateLZ": rateLZ,
    "probWC_eff": probWC_eff,
    "airy_arg_WC_rate": airy_arg_WC_rate,
    "Ai_WC_rate": Ai_WC_rate,
    "nosWC_eff": nosWC_eff,
    "rateWC_eff": rateWC_eff,
    "probWC": probWC,
    "nosWC": nosWC,
    "rateWC": rateWC,
    "k_LZ_intermediate": k_LZ_intermediate,
    "N_LZ_intermediate": N_LZ_intermediate,
    "P_LZ_intermediate_rate_grid": P_LZ_intermediate_rate_grid,
    "k_WC_intermediate": k_WC_intermediate,
    "N_WC_intermediate": N_WC_intermediate,
    "P_WC_intermediate_rate_grid": P_WC_intermediate_rate_grid,
    "k_LZ_channels": k_LZ_channels,
    "N_LZ_channels": N_LZ_channels,
    "P_LZ_channels_rate_grid": P_LZ_channels_rate_grid,
    "k_WC_channels": k_WC_channels,
    "N_WC_channels": N_WC_channels,
    "P_WC_channels_rate_grid": P_WC_channels_rate_grid,
    "k_LZ_channel_groups": k_LZ_channel_groups,
    "N_LZ_channel_groups": N_LZ_channel_groups,
    "P_LZ_channel_groups_rate_grid": P_LZ_channel_groups_rate_grid,
    "k_WC_channel_groups": k_WC_channel_groups,
    "N_WC_channel_groups": N_WC_channel_groups,
    "P_WC_channel_groups_rate_grid": P_WC_channel_groups_rate_grid,
    "E_rate_LZ_cm1": E_rate_LZ_cm1,
    "E_rate_WC_cm1": E_rate_WC_cm1,
    "rho_reactant_cm": rho_reactant_cm,
    "rho_MECP_internal_cm": rho_MECP_internal_cm,
    "rho_MECP_shifted_plot": rho_MECP_shifted_plot,
    "P_LZ_rate_grid": P_LZ_rate_grid,
    "gamma_LZ_rate_grid": gamma_LZ_rate_grid,
    "v_bohr_s_rate_grid": v_bohr_s_rate_grid,
    "N_MECP_LZ": N_MECP_LZ,
    "k_LZ_micro_s_inv": k_LZ_micro_s_inv,
    "k_LZ_effective_micro_s_inv": k_LZ_effective_micro_s_inv,
    "N_MECP_LZ_effective": N_MECP_LZ_effective,
    "log_k_LZ_raw": log_k_LZ_raw,
    "E_LZ_log_raw_cm1": E_LZ_log_raw_cm1,
    "P_WC_rate_grid": P_WC_rate_grid,
    "N_MECP_WC": N_MECP_WC,
    "k_WC_micro_s_inv": k_WC_micro_s_inv,
    "k_WC_effective_micro_s_inv": k_WC_effective_micro_s_inv,
    "N_MECP_WC_effective": N_MECP_WC_effective,
    "log_k_WC_raw": log_k_WC_raw,
    "E_WC_log_raw_cm1": E_WC_log_raw_cm1,
    "nosLZ_NAST": nosLZ_NAST,
    "rateLZ_NAST": rateLZ_NAST,
    "nosWC_NAST": nosWC_NAST,
    "rateWC_NAST": rateWC_NAST,
    "ms_tex_value": ms_tex_value,
    "make_rate_group_legend": make_rate_group_legend,
    "local_step9": local_step9,
    "remote_step9": remote_step9,
    "plot_files_step9": plot_files_step9,
    "summary_file_step9": summary_file_step9,
    "rate_grid_file_step9": rate_grid_file_step9,
    "intermediate_rate_summary_file_step9": intermediate_rate_summary_file_step9,
    "channel_rate_summary_file_step9": channel_rate_summary_file_step9,
    "dos_file_step9": dos_file_step9,
    "remote_summary_file_step9": remote_summary_file_step9,
    "remote_rate_grid_file_step9": remote_rate_grid_file_step9,
    "remote_intermediate_rate_summary_file_step9": remote_intermediate_rate_summary_file_step9,
    "remote_channel_rate_summary_file_step9": remote_channel_rate_summary_file_step9,
    "remote_dos_file_step9": remote_dos_file_step9,
    "remote_plot_files_step9": remote_plot_files_step9
})

# ============================================================
# Final summary
# ============================================================

section("STEP 9 SUMMARY")

print(f"Active MECP barrier               : {E_MECP:.6f} cm^-1")
print(f"Active MECP barrier               : {E_MECP * kJmol_per_cm1:.6f} kJ/mol")
print(f"Effective H_SO_cm                 : {float(H_SO_cm):.6f} cm^-1")
print(f"Reactant frequencies used         : {len(freq_R)}")
print(f"MECP frequencies used             : {len(freq_X)}")
print(f"k_LZ effective range              : {np.nanmin(k_LZ_micro_s_inv):.6e} to {np.nanmax(k_LZ_micro_s_inv):.6e}")
print(f"k_WC effective range              : {np.nanmin(k_WC_micro_s_inv):.6e} to {np.nanmax(k_WC_micro_s_inv):.6e}")
print(f"LZ intermediate curves            : {len(k_LZ_intermediate)}")
print(f"WC intermediate curves            : {len(k_WC_intermediate)}")
print(f"LZ individual MS channels         : {len(k_LZ_channels)}")
print(f"WC individual MS channels         : {len(k_WC_channels)}")
print(f"LZ grouped MS channels            : {len(k_LZ_channel_groups)}")
print(f"WC grouped MS channels            : {len(k_WC_channel_groups)}")

print("\nImportant variables available for Step 10:")
print("  E_rate_LZ_cm1")
print("  E_rate_WC_cm1")
print("  rho_reactant_cm")
print("  k_LZ_effective_micro_s_inv")
print("  k_WC_effective_micro_s_inv")
print("  k_LZ_intermediate")
print("  k_WC_intermediate")
print("  k_LZ_channel_groups  # representative curves only; no sum")
print("  k_WC_channel_groups  # representative curves only; no sum")
print("  summary_file_step9")
print("  rate_grid_file_step9")
print("  intermediate_rate_summary_file_step9")
print("  channel_rate_summary_file_step9")
print("  dos_file_step9")
print("  plot_files_step9")

print("\nSTEP 9 COMPLETED SUCCESSFULLY.\n")

#%% STEP 10. CLUSTER CANONICAL EFFECTIVE + INTERMEDIATE + MS-SPECIFIC LZ/WC RATES

import os
import posixpath
import numpy as np
import matplotlib.pyplot as plt

print(r'''
====================================================================
 STEP 10 | CLUSTER VERSION
 Canonical effective, intermediate, and MS-specific LZ/WC rates
====================================================================

This step canonical-averages the Step 9 microcanonical NAST rates:

  k(T) = Σ k(E) rho_R(E) exp[-E/(kBT)] ΔE
         ------------------------------------
         Σ rho_R(E) exp[-E/(kBT)] ΔE

Outputs include:

  1. Effective canonical LZ/WC rates
  2. Intermediate canonical LZ/WC rates: k^0, k^1, k^1/2, ...
  3. Symmetry-collapsed MS-specific canonical LZ/WC rates:
     k^{Ms_LS,Ms_HS}

Every nonzero MS-specific channel is canonical-averaged independently.
Channels with identical |H_SO| values generate identical microcanonical
and canonical rate curves; only one representative curve is plotted for
such channels. No MS-specific rates are summed.
''')

# ============================================================
# Required variables
# ============================================================

required_vars_step10 = [
    "jobname",
    "E_rate_LZ_cm1",
    "E_rate_WC_cm1",
    "rho_reactant_cm",
    "k_LZ_effective_micro_s_inv",
    "k_WC_effective_micro_s_inv",
    "local_base",
    "remote_base"
]

for var in required_vars_step10:
    if var not in globals():
        raise RuntimeError(
            f"Required variable '{var}' is missing. Run cluster Step 9 first."
        )

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = "MECP_ONLY"

SOC_EFFECTIVE_ONLY = bool(globals().get("SOC_EFFECTIVE_ONLY", False))
SOC_MATRIX_CHANNELS_AVAILABLE = bool(globals().get("SOC_MATRIX_CHANNELS_AVAILABLE", True))

if SOC_EFFECTIVE_ONLY or not SOC_MATRIX_CHANNELS_AVAILABLE:
    print("\nEffective-only SOC mode active.")
    print("Only effective LZ/WC rates will be computed/plotted.")
    
# ============================================================
# Helpers
# ============================================================

def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def ask_float(prompt, default=None, minimum=None):
    while True:
        ans = input(prompt).strip()

        if ans == "" and default is not None:
            val = float(default)
        else:
            try:
                val = float(ans)
            except ValueError:
                print("  Please enter a valid numerical value.")
                continue

        if minimum is not None and val < minimum:
            print(f"  Please enter a value greater than or equal to {minimum}.")
            continue

        return val


def ask_int(prompt, default=None, minimum=None):
    while True:
        ans = input(prompt).strip()

        if ans == "" and default is not None:
            val = int(default)
        else:
            try:
                val = int(ans)
            except ValueError:
                print("  Please enter an integer.")
                continue

        if minimum is not None and val < minimum:
            print(f"  Please enter an integer greater than or equal to {minimum}.")
            continue

        return val


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def save_current_figure(path):
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.show()


def ms_tex_value(x):
    x = float(x)

    if abs(x - round(x)) < 1.0e-8:
        return str(int(round(x)))

    if abs(abs(x) - 0.5) < 1.0e-8:
        return r"\frac{1}{2}" if x > 0 else r"-\frac{1}{2}"

    if abs(abs(x) - 1.5) < 1.0e-8:
        return r"\frac{3}{2}" if x > 0 else r"-\frac{3}{2}"

    if abs(abs(x) - 2.5) < 1.0e-8:
        return r"\frac{5}{2}" if x > 0 else r"-\frac{5}{2}"

    if abs(abs(x) - 3.5) < 1.0e-8:
        return r"\frac{7}{2}" if x > 0 else r"-\frac{7}{2}"

    return f"{x:.1f}"


def make_canonical_group_legend(prefix, group_data):
    """
    Build a legend for one representative MS-specific canonical curve.

    Multiple symbols indicate symmetry-equivalent channels with identical
    |H_SO| values and therefore identical canonical rates. Commas indicate
    coincident curves; no summation is implied.
    """
    symbols = group_data.get("channel_symbols", [])
    H_abs = float(group_data["H_abs_cm1"])

    rate_symbols = [sym.replace("$P", "$k") for sym in symbols]

    if len(rate_symbols) == 0:
        return f"{prefix} MS |H|={H_abs:.1f}"

    if len(rate_symbols) <= 3:
        return (
            f"{prefix} "
            + ", ".join(rate_symbols)
            + f", |H|={H_abs:.1f}"
        )

    return (
        f"{prefix} "
        + ", ".join(rate_symbols[:3])
        + f", ..., |H|={H_abs:.1f}, "
        + f"equivalent channels={len(rate_symbols)}"
    )


# ============================================================
# Temperature grid
# ============================================================

section("CANONICAL TEMPERATURE GRID")

kB_cm1_per_K = 0.695034800

print("Canonical averaging formula:")
print("  k(T) = Σ k(E) rho_R(E) exp[-E/(kBT)] ΔE / Σ rho_R(E) exp[-E/(kBT)] ΔE")
print("  Energies are relative to the reactant/reference minimum in cm^-1.\n")

T_min = ask_float(
    "Minimum temperature for canonical rates, K, e.g. 100: ",
    minimum=1.0e-12
)

T_max = ask_float(
    "Maximum temperature for canonical rates, K, e.g. 1000: ",
    minimum=1.0e-12
)

nT = ask_int(
    "Number of temperature points, e.g. 100: ",
    minimum=2
)

if T_max <= T_min:
    raise RuntimeError("Invalid temperature range: T_max must be greater than T_min.")

T_grid_K = np.linspace(T_min, T_max, nT)

E_rate_LZ_cm1 = np.asarray(E_rate_LZ_cm1, dtype=float).reshape(-1)
E_rate_WC_cm1 = np.asarray(E_rate_WC_cm1, dtype=float).reshape(-1)
rho_reactant_cm = np.asarray(rho_reactant_cm, dtype=float).reshape(-1)

k_LZ_effective_micro_s_inv = np.asarray(
    k_LZ_effective_micro_s_inv,
    dtype=float
).reshape(-1)

k_WC_effective_micro_s_inv = np.asarray(
    k_WC_effective_micro_s_inv,
    dtype=float
).reshape(-1)

if E_rate_LZ_cm1.size != rho_reactant_cm.size:
    raise RuntimeError("E_rate_LZ_cm1 and rho_reactant_cm have different sizes.")

if E_rate_WC_cm1.size != rho_reactant_cm.size:
    raise RuntimeError("E_rate_WC_cm1 and rho_reactant_cm have different sizes.")

if E_rate_LZ_cm1.size != k_LZ_effective_micro_s_inv.size:
    raise RuntimeError("E_rate_LZ_cm1 and k_LZ_effective_micro_s_inv have different sizes.")

if E_rate_WC_cm1.size != k_WC_effective_micro_s_inv.size:
    raise RuntimeError("E_rate_WC_cm1 and k_WC_effective_micro_s_inv have different sizes.")

dE_LZ = float(np.mean(np.diff(E_rate_LZ_cm1))) if E_rate_LZ_cm1.size > 1 else 1.0
dE_WC = float(np.mean(np.diff(E_rate_WC_cm1))) if E_rate_WC_cm1.size > 1 else 1.0

print(f"RUN_MODE                      : {RUN_MODE}")
print(f"workflow_mode                 : {workflow_mode}")
print(f"T range                       : {T_grid_K[0]:.3f} to {T_grid_K[-1]:.3f} K")
print(f"Number of T points            : {nT}")
print(f"dE_LZ                         : {dE_LZ:.6f} cm^-1")
print(f"dE_WC                         : {dE_WC:.6f} cm^-1")
print(f"LZ energy grid                : {E_rate_LZ_cm1[0]:.3f} to {E_rate_LZ_cm1[-1]:.3f} cm^-1")
print(f"WC energy grid                : {E_rate_WC_cm1[0]:.3f} to {E_rate_WC_cm1[-1]:.3f} cm^-1")

# ============================================================
# Canonical averaging helper
# ============================================================

section("CANONICAL AVERAGING HELPER")

def canonical_rate_from_microcanonical(E_cm1, rho_E, k_E, T_grid, dE):
    E_cm1 = np.asarray(E_cm1, dtype=float).reshape(-1)
    rho_E = np.asarray(rho_E, dtype=float).reshape(-1)
    k_E = np.asarray(k_E, dtype=float).reshape(-1)
    T_grid = np.asarray(T_grid, dtype=float).reshape(-1)

    if E_cm1.shape != rho_E.shape or E_cm1.shape != k_E.shape:
        raise RuntimeError("E, rho(E), and k(E) arrays must have the same shape.")

    kT_cm1 = kB_cm1_per_K * T_grid

    kT_rate = np.zeros_like(T_grid, dtype=float)
    Q_R = np.zeros_like(T_grid, dtype=float)

    for iT, kT in enumerate(kT_cm1):
        boltz = np.exp(-E_cm1 / kT)

        denom = np.sum(rho_E * boltz) * dE
        numer = np.sum(k_E * rho_E * boltz) * dE

        Q_R[iT] = denom

        if denom > 0.0 and np.isfinite(denom):
            kT_rate[iT] = numer / denom
        else:
            kT_rate[iT] = 0.0

    kT_rate[~np.isfinite(kT_rate)] = 0.0
    kT_rate = np.clip(kT_rate, 0.0, None)

    return kT_rate, Q_R


print("canonical_rate_from_microcanonical prepared.")

# ============================================================
# Effective canonical rates
# ============================================================

section("EFFECTIVE CANONICAL LZ/WC RATES")

k_LZ_canonical_s_inv, Q_R_LZ = canonical_rate_from_microcanonical(
    E_rate_LZ_cm1,
    rho_reactant_cm,
    k_LZ_effective_micro_s_inv,
    T_grid_K,
    dE_LZ
)

k_WC_canonical_s_inv, Q_R_WC = canonical_rate_from_microcanonical(
    E_rate_WC_cm1,
    rho_reactant_cm,
    k_WC_effective_micro_s_inv,
    T_grid_K,
    dE_WC
)

kcanon_LZ = k_LZ_canonical_s_inv
kcanon_WC = k_WC_canonical_s_inv

print(f"k_LZ canonical range        : {np.nanmin(k_LZ_canonical_s_inv):.6e} to {np.nanmax(k_LZ_canonical_s_inv):.6e} s^-1")
print(f"k_WC canonical range        : {np.nanmin(k_WC_canonical_s_inv):.6e} to {np.nanmax(k_WC_canonical_s_inv):.6e} s^-1")

# ============================================================
# Intermediate canonical rates
# ============================================================

section("CANONICAL INTERMEDIATE RATES")

k_LZ_intermediate_canonical = {}
k_WC_intermediate_canonical = {}

if "k_LZ_intermediate" in globals() and len(k_LZ_intermediate) > 0:
    for key, data in k_LZ_intermediate.items():
        k_E = np.asarray(data["k"][1:], dtype=float).reshape(-1)

        if k_E.size == E_rate_LZ_cm1.size:
            k_T, _ = canonical_rate_from_microcanonical(
                E_rate_LZ_cm1,
                rho_reactant_cm,
                k_E,
                T_grid_K,
                dE_LZ
            )

            k_LZ_intermediate_canonical[key] = {
                "H_int_cm1": data["H_int_cm1"],
                "abs_Ms_low": data["abs_Ms_low"],
                "degeneracy": data["degeneracy"],
                "k_T": k_T
            }

if "k_WC_intermediate" in globals() and len(k_WC_intermediate) > 0:
    for key, data in k_WC_intermediate.items():
        k_E = np.asarray(data["k"][1:], dtype=float).reshape(-1)

        if k_E.size == E_rate_WC_cm1.size:
            k_T, _ = canonical_rate_from_microcanonical(
                E_rate_WC_cm1,
                rho_reactant_cm,
                k_E,
                T_grid_K,
                dE_WC
            )

            k_WC_intermediate_canonical[key] = {
                "H_int_cm1": data["H_int_cm1"],
                "abs_Ms_low": data["abs_Ms_low"],
                "degeneracy": data["degeneracy"],
                "k_T": k_T
            }

print(f"LZ intermediate canonical curves : {len(k_LZ_intermediate_canonical)}")
print(f"WC intermediate canonical curves : {len(k_WC_intermediate_canonical)}")

# ============================================================
# Symmetry-collapsed MS-specific canonical rates
# ============================================================

section("CANONICAL SYMMETRY-COLLAPSED MS-SPECIFIC RATES")

# Step 9 stores one representative microcanonical rate curve for every
# set of channels having identical |H_SO| values. Here each representative
# curve is canonical-averaged directly. No channel rates are summed.

k_LZ_channel_groups_canonical = {}
k_WC_channel_groups_canonical = {}

if "k_LZ_channel_groups" in globals() and len(k_LZ_channel_groups) > 0:
    for key, data in k_LZ_channel_groups.items():
        if "k_representative" not in data:
            raise RuntimeError(
                f"LZ channel group {key} does not contain "
                "'k_representative'. Run the modified Step 9 first."
            )

        k_group_E = np.asarray(
            data["k_representative"][1:],
            dtype=float
        ).reshape(-1)

        if k_group_E.size != E_rate_LZ_cm1.size:
            raise RuntimeError(
                f"LZ representative group {key} has length "
                f"{k_group_E.size}, expected {E_rate_LZ_cm1.size}."
            )

        k_group_T, _ = canonical_rate_from_microcanonical(
            E_rate_LZ_cm1,
            rho_reactant_cm,
            k_group_E,
            T_grid_K,
            dE_LZ
        )

        k_LZ_channel_groups_canonical[key] = {
            "H_abs_cm1": float(data["H_abs_cm1"]),
            "degeneracy": int(data.get("degeneracy", 1)),
            "channel_symbols": list(
                data.get("channel_symbols", [])
            ),
            "labels": list(data.get("labels", [])),
            "representative_label": data.get(
                "representative_label"
            ),
            "combination_rule": "representative_only_no_sum",
            "k_T": k_group_T
        }

if "k_WC_channel_groups" in globals() and len(k_WC_channel_groups) > 0:
    for key, data in k_WC_channel_groups.items():
        if "k_representative" not in data:
            raise RuntimeError(
                f"WC channel group {key} does not contain "
                "'k_representative'. Run the modified Step 9 first."
            )

        k_group_E = np.asarray(
            data["k_representative"][1:],
            dtype=float
        ).reshape(-1)

        if k_group_E.size != E_rate_WC_cm1.size:
            raise RuntimeError(
                f"WC representative group {key} has length "
                f"{k_group_E.size}, expected {E_rate_WC_cm1.size}."
            )

        k_group_T, _ = canonical_rate_from_microcanonical(
            E_rate_WC_cm1,
            rho_reactant_cm,
            k_group_E,
            T_grid_K,
            dE_WC
        )

        k_WC_channel_groups_canonical[key] = {
            "H_abs_cm1": float(data["H_abs_cm1"]),
            "degeneracy": int(data.get("degeneracy", 1)),
            "channel_symbols": list(
                data.get("channel_symbols", [])
            ),
            "labels": list(data.get("labels", [])),
            "representative_label": data.get(
                "representative_label"
            ),
            "combination_rule": "representative_only_no_sum",
            "k_T": k_group_T
        }

print(
    f"Representative LZ MS-specific canonical curves : "
    f"{len(k_LZ_channel_groups_canonical)}"
)
print(
    f"Representative WC MS-specific canonical curves : "
    f"{len(k_WC_channel_groups_canonical)}"
)
print("MS-specific canonical combination rule          : no summation")

# ============================================================
# Plots
# ============================================================

section("PLOTTING CANONICAL RATES")

local_step10 = os.path.join(local_base, "Canonical_rates")
os.makedirs(local_step10, exist_ok=True)

remote_step10 = posixpath.join(remote_base, "Canonical_rates")

if "remote_mkdir_p" in globals() and "sftp" in globals():
    try:
        remote_mkdir_p(sftp, remote_step10)
    except Exception:
        pass

plot_files_step10 = {}

def register_plot(name):
    path = os.path.join(local_step10, f"{jobname}_Step10_{name}.png")
    plot_files_step10[name] = path
    return path

# ----------------------------
# k(T) plot
# ----------------------------

plt.figure(figsize=(9.2, 5.2))
plt.plot(T_grid_K, k_LZ_canonical_s_inv, linewidth=2.8, linestyle=":", color="k", label="LZ $k^{eff}$")
plt.plot(T_grid_K, k_WC_canonical_s_inv, linewidth=2.8, linestyle="-", color="k", label="WC $k^{eff}$")

for key, data in sorted(
    k_LZ_intermediate_canonical.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    plt.plot(
        T_grid_K,
        data["k_T"],
        linewidth=2.0,
        linestyle="--",
        label=(
            f"LZ $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_WC_intermediate_canonical.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    plt.plot(
        T_grid_K,
        data["k_T"],
        linewidth=2.0,
        linestyle="--",
        label=(
            f"WC $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_LZ_channel_groups_canonical.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    plt.plot(
        T_grid_K,
        data["k_T"],
        linewidth=1.4,
        label=make_canonical_group_legend("LZ", data)
    )

for key, data in sorted(
    k_WC_channel_groups_canonical.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    plt.plot(
        T_grid_K,
        data["k_T"],
        linewidth=1.4,
        label=make_canonical_group_legend("WC", data)
    )

plt.xlabel("Temperature (K)")
plt.ylabel(r"Canonical rate constant $k(T)$ (s$^{-1}$)")
plt.title("Canonical Effective, Intermediate, and MS-specific LZ/WC Rates")
plt.grid(False)
plt.legend(fontsize=7)
save_current_figure(register_plot("canonical_effective_intermediate_MS_rates"))

# ----------------------------
# Arrhenius-style plot
# ----------------------------

plt.figure(figsize=(9.2, 5.2))

mask_lz = k_LZ_canonical_s_inv > 0.0
mask_wc = k_WC_canonical_s_inv > 0.0

plt.plot(
    1000.0 / T_grid_K[mask_lz],
    np.log10(k_LZ_canonical_s_inv[mask_lz]),
    linewidth=2.3,
    linestyle=":",
    color="k",
    label="LZ $k^{eff}$"
)

plt.plot(
    1000.0 / T_grid_K[mask_wc],
    np.log10(k_WC_canonical_s_inv[mask_wc]),
    linewidth=2.3,
    linestyle="-",
    color="k",
    label="WC $k^{eff}$"
)

for key, data in sorted(
    k_LZ_intermediate_canonical.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    k_T = data["k_T"]
    mask = k_T > 0.0

    plt.plot(
        1000.0 / T_grid_K[mask],
        np.log10(k_T[mask]),
        linewidth=2.0,
        linestyle="--",
        label=(
            f"LZ $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_WC_intermediate_canonical.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    k_T = data["k_T"]
    mask = k_T > 0.0

    plt.plot(
        1000.0 / T_grid_K[mask],
        np.log10(k_T[mask]),
        linewidth=2.0,
        linestyle="--",
        label=(
            f"WC $k^{{{ms_tex_value(data['abs_Ms_low'])}}}$ "
            f"($SOC_{{int}}$={data['H_int_cm1']:.1f})"
        )
    )

for key, data in sorted(
    k_LZ_channel_groups_canonical.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    k_T = data["k_T"]
    mask = k_T > 0.0

    plt.plot(
        1000.0 / T_grid_K[mask],
        np.log10(k_T[mask]),
        linewidth=1.4,
        label=make_canonical_group_legend("LZ", data)
    )

for key, data in sorted(
    k_WC_channel_groups_canonical.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    k_T = data["k_T"]
    mask = k_T > 0.0

    plt.plot(
        1000.0 / T_grid_K[mask],
        np.log10(k_T[mask]),
        linewidth=1.4,
        label=make_canonical_group_legend("WC", data)
    )

plt.xlabel(r"$1000/T$ (K$^{-1}$)")
plt.ylabel(r"$\log_{10}[k(T)]$")
plt.title("Arrhenius-style Canonical Effective, Intermediate, and MS-specific Rates")
plt.grid(False)
plt.legend(fontsize=7)
save_current_figure(register_plot("arrhenius_effective_intermediate_MS_rates"))

# ============================================================
# Save tables
# ============================================================

section("SAVING STEP 10 OUTPUTS")

canonical_rate_file = os.path.join(
    local_step10,
    f"{jobname}_Step10_canonical_LZ_WC_rates.txt"
)

summary_file_step10 = os.path.join(
    local_step10,
    f"{jobname}_Step10_canonical_rate_summary.txt"
)

intermediate_canonical_file_step10 = os.path.join(
    local_step10,
    f"{jobname}_Step10_intermediate_canonical_rates.txt"
)

representative_canonical_file_step10 = os.path.join(
    local_step10,
    f"{jobname}_Step10_representative_MS_canonical_rates.txt"
)

table_columns = [
    T_grid_K,
    1000.0 / T_grid_K,
    k_LZ_canonical_s_inv,
    k_WC_canonical_s_inv,
    Q_R_LZ,
    Q_R_WC
]

header_items = [
    "T_K",
    "1000_over_T_Kinv",
    "k_LZ_effective_s^-1",
    "k_WC_effective_s^-1",
    "Q_R_LZ",
    "Q_R_WC"
]

canonical_rate_table = np.column_stack(table_columns)
canonical_rate_header = "  ".join(header_items)

np.savetxt(
    canonical_rate_file,
    canonical_rate_table,
    header=canonical_rate_header,
    fmt="%.10e"
)

summary_lines = []
summary_lines.append("Step 10 canonical effective, intermediate, and MS-specific LZ/WC rate constants")
summary_lines.append(f"Run mode = {RUN_MODE}")
summary_lines.append(f"Workflow mode = {workflow_mode}")
summary_lines.append("")
summary_lines.append("[TEMPERATURE_GRID]")
summary_lines.append(f"T_min_K = {T_grid_K[0]:.12f}")
summary_lines.append(f"T_max_K = {T_grid_K[-1]:.12f}")
summary_lines.append(f"nT = {nT}")
summary_lines.append("")
summary_lines.append("[ENERGY_GRID]")
summary_lines.append(f"dE_LZ_cm1 = {dE_LZ:.12f}")
summary_lines.append(f"dE_WC_cm1 = {dE_WC:.12f}")
summary_lines.append(f"E_LZ_min_cm1 = {E_rate_LZ_cm1[0]:.12f}")
summary_lines.append(f"E_LZ_max_cm1 = {E_rate_LZ_cm1[-1]:.12f}")
summary_lines.append(f"E_WC_min_cm1 = {E_rate_WC_cm1[0]:.12f}")
summary_lines.append(f"E_WC_max_cm1 = {E_rate_WC_cm1[-1]:.12f}")
summary_lines.append("")
summary_lines.append("[EFFECTIVE_CANONICAL_RANGES]")
summary_lines.append(f"k_LZ_min_s_inv = {np.nanmin(k_LZ_canonical_s_inv):.12e}")
summary_lines.append(f"k_LZ_max_s_inv = {np.nanmax(k_LZ_canonical_s_inv):.12e}")
summary_lines.append(f"k_WC_min_s_inv = {np.nanmin(k_WC_canonical_s_inv):.12e}")
summary_lines.append(f"k_WC_max_s_inv = {np.nanmax(k_WC_canonical_s_inv):.12e}")
summary_lines.append("")
summary_lines.append("[INTERMEDIATE]")
summary_lines.append(f"LZ_intermediate_canonical_count = {len(k_LZ_intermediate_canonical)}")
summary_lines.append(f"WC_intermediate_canonical_count = {len(k_WC_intermediate_canonical)}")
summary_lines.append("")
summary_lines.append("[MS_SPECIFIC_REPRESENTATIVE_GROUPS_NO_SUM]")
summary_lines.append(f"LZ_representative_canonical_count = {len(k_LZ_channel_groups_canonical)}")
summary_lines.append(f"WC_representative_canonical_count = {len(k_WC_channel_groups_canonical)}")
summary_lines.append("")
summary_lines.append("[FILES]")
summary_lines.append(f"canonical_rate_file = {canonical_rate_file}")
summary_lines.append(f"intermediate_canonical_file_step10 = {intermediate_canonical_file_step10}")
summary_lines.append(f"representative_canonical_file_step10 = {representative_canonical_file_step10}")

for name, path in plot_files_step10.items():
    summary_lines.append(f"plot_{name} = {path}")

write_local_text(summary_file_step10, "\n".join(summary_lines) + "\n")

intermediate_lines = []
intermediate_lines.append("Step 10 intermediate canonical rates")
intermediate_lines.append("")
intermediate_lines.append("[LZ_INTERMEDIATE]")
intermediate_lines.append("label  abs_Ms_low  degeneracy  H_int_cm1  k_min_s_inv  k_max_s_inv")

for key, data in sorted(
    k_LZ_intermediate_canonical.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    kT = data["k_T"]

    intermediate_lines.append(
        f"{key:20s} "
        f"{data['abs_Ms_low']:12.6f} "
        f"{data['degeneracy']:5d} "
        f"{data['H_int_cm1']:18.10f} "
        f"{np.nanmin(kT):18.10e} "
        f"{np.nanmax(kT):18.10e}"
    )

intermediate_lines.append("")
intermediate_lines.append("[WC_INTERMEDIATE]")
intermediate_lines.append("label  abs_Ms_low  degeneracy  H_int_cm1  k_min_s_inv  k_max_s_inv")

for key, data in sorted(
    k_WC_intermediate_canonical.items(),
    key=lambda x: x[1]["abs_Ms_low"]
):
    kT = data["k_T"]

    intermediate_lines.append(
        f"{key:20s} "
        f"{data['abs_Ms_low']:12.6f} "
        f"{data['degeneracy']:5d} "
        f"{data['H_int_cm1']:18.10f} "
        f"{np.nanmin(kT):18.10e} "
        f"{np.nanmax(kT):18.10e}"
    )

write_local_text(intermediate_canonical_file_step10, "\n".join(intermediate_lines) + "\n")

representative_lines = []
representative_lines.append("Step 10 representative MS-specific canonical rates; no summation")
representative_lines.append("")
representative_lines.append("[LZ_REPRESENTATIVE_GROUPS_NO_SUM]")
representative_lines.append("group_key  H_abs_cm1  equivalent_count  symbols  representative_label  k_min_s_inv  k_max_s_inv")

for key, data in sorted(
    k_LZ_channel_groups_canonical.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    kT = data["k_T"]

    representative_lines.append(
        f"{key:16s} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['degeneracy']:5d} "
        f"{', '.join(data.get('channel_symbols', [])):30s} "
        f"{str(data.get('representative_label')):50s} "
        f"{np.nanmin(kT):18.10e} "
        f"{np.nanmax(kT):18.10e}"
    )

representative_lines.append("")
representative_lines.append("[WC_REPRESENTATIVE_GROUPS_NO_SUM]")
representative_lines.append("group_key  H_abs_cm1  equivalent_count  symbols  representative_label  k_min_s_inv  k_max_s_inv")

for key, data in sorted(
    k_WC_channel_groups_canonical.items(),
    key=lambda x: x[1]["H_abs_cm1"],
    reverse=True
):
    kT = data["k_T"]

    representative_lines.append(
        f"{key:16s} "
        f"{data['H_abs_cm1']:18.10f} "
        f"{data['degeneracy']:5d} "
        f"{', '.join(data.get('channel_symbols', [])):30s} "
        f"{str(data.get('representative_label')):50s} "
        f"{np.nanmin(kT):18.10e} "
        f"{np.nanmax(kT):18.10e}"
    )

write_local_text(
    representative_canonical_file_step10,
    "\n".join(representative_lines) + "\n"
)

remote_canonical_rate_file = None
remote_summary_file_step10 = None
remote_intermediate_canonical_file_step10 = None
remote_representative_canonical_file_step10 = None
remote_plot_files_step10 = {}

if "sftp" in globals():
    try:
        remote_canonical_rate_file = posixpath.join(
            remote_step10,
            posixpath.basename(canonical_rate_file)
        )
        remote_summary_file_step10 = posixpath.join(
            remote_step10,
            posixpath.basename(summary_file_step10)
        )
        remote_intermediate_canonical_file_step10 = posixpath.join(
            remote_step10,
            posixpath.basename(intermediate_canonical_file_step10)
        )
        remote_representative_canonical_file_step10 = posixpath.join(
            remote_step10,
            posixpath.basename(representative_canonical_file_step10)
        )

        sftp.put(canonical_rate_file, remote_canonical_rate_file)
        sftp.put(summary_file_step10, remote_summary_file_step10)
        sftp.put(intermediate_canonical_file_step10, remote_intermediate_canonical_file_step10)
        sftp.put(
            representative_canonical_file_step10,
            remote_representative_canonical_file_step10
        )

        for name, local_path in plot_files_step10.items():
            remote_path = posixpath.join(
                remote_step10,
                posixpath.basename(local_path)
            )
            sftp.put(local_path, remote_path)
            remote_plot_files_step10[name] = remote_path

    except Exception:
        remote_canonical_rate_file = None
        remote_summary_file_step10 = None
        remote_intermediate_canonical_file_step10 = None
        remote_representative_canonical_file_step10 = None
        remote_plot_files_step10 = {}

print(f"Local canonical rate file          : {canonical_rate_file}")
print(f"Local Step 10 summary              : {summary_file_step10}")
print(f"Local intermediate canonical file  : {intermediate_canonical_file_step10}")
print(f"Local representative MS file       : {representative_canonical_file_step10}")

for name, path in plot_files_step10.items():
    print(f"Local plot {name:40s}: {path}")

if remote_canonical_rate_file:
    print(f"Remote canonical rate file         : {remote_canonical_rate_file}")
    print(f"Remote Step 10 summary             : {remote_summary_file_step10}")
    print(f"Remote intermediate canonical file : {remote_intermediate_canonical_file_step10}")
    print(f"Remote representative MS file      : {remote_representative_canonical_file_step10}")

# ============================================================
# Export variables
# ============================================================

globals().update({
    "RUN_MODE": RUN_MODE,
    "workflow_mode": workflow_mode,
    "kB_cm1_per_K": kB_cm1_per_K,
    "T_grid_K": T_grid_K,
    "T_min": T_min,
    "T_max": T_max,
    "nT": nT,
    "dE_LZ": dE_LZ,
    "dE_WC": dE_WC,
    "canonical_rate_from_microcanonical": canonical_rate_from_microcanonical,

    "k_LZ_canonical_s_inv": k_LZ_canonical_s_inv,
    "k_WC_canonical_s_inv": k_WC_canonical_s_inv,
    "kcanon_LZ": kcanon_LZ,
    "kcanon_WC": kcanon_WC,
    "Q_R_LZ": Q_R_LZ,
    "Q_R_WC": Q_R_WC,

    "k_LZ_intermediate_canonical": k_LZ_intermediate_canonical,
    "k_WC_intermediate_canonical": k_WC_intermediate_canonical,
    "k_LZ_channel_groups_canonical": k_LZ_channel_groups_canonical,
    "k_WC_channel_groups_canonical": k_WC_channel_groups_canonical,

    "ms_tex_value": ms_tex_value,
    "make_canonical_group_legend": make_canonical_group_legend,

    "local_step10": local_step10,
    "remote_step10": remote_step10,
    "plot_files_step10": plot_files_step10,
    "canonical_rate_file": canonical_rate_file,
    "summary_file_step10": summary_file_step10,
    "intermediate_canonical_file_step10": intermediate_canonical_file_step10,
    "representative_canonical_file_step10": representative_canonical_file_step10,
    "remote_canonical_rate_file": remote_canonical_rate_file,
    "remote_summary_file_step10": remote_summary_file_step10,
    "remote_intermediate_canonical_file_step10": remote_intermediate_canonical_file_step10,
    "remote_representative_canonical_file_step10": remote_representative_canonical_file_step10,
    "remote_plot_files_step10": remote_plot_files_step10
})

# ============================================================
# Final summary
# ============================================================

section("STEP 10 SUMMARY")

print(f"T range                         : {T_grid_K[0]:.2f} to {T_grid_K[-1]:.2f} K")
print(f"k_LZ effective canonical         : {np.nanmin(k_LZ_canonical_s_inv):.6e} to {np.nanmax(k_LZ_canonical_s_inv):.6e} s^-1")
print(f"k_WC effective canonical         : {np.nanmin(k_WC_canonical_s_inv):.6e} to {np.nanmax(k_WC_canonical_s_inv):.6e} s^-1")
print(f"LZ intermediate canonical curves : {len(k_LZ_intermediate_canonical)}")
print(f"WC intermediate canonical curves : {len(k_WC_intermediate_canonical)}")
print(f"LZ representative MS curves      : {len(k_LZ_channel_groups_canonical)}")
print(f"WC representative MS curves      : {len(k_WC_channel_groups_canonical)}")

print("\nImportant variables available for later steps:")
print("  T_grid_K")
print("  k_LZ_canonical_s_inv")
print("  k_WC_canonical_s_inv")
print("  k_LZ_intermediate_canonical")
print("  k_WC_intermediate_canonical")
print("  k_LZ_channel_groups_canonical")
print("  k_WC_channel_groups_canonical")
print("  canonical_rate_file")
print("  summary_file_step10")
print("  intermediate_canonical_file_step10")
print("  representative_canonical_file_step10")
print("  plot_files_step10")

print("\nSTEP 10 COMPLETED SUCCESSFULLY.\n")

#%% STEP 11. CONDITIONAL NAST-CONVENTION WC PROBABILITY/RATE CORRECTION

import os
import posixpath
import numpy as np
import matplotlib.pyplot as plt
from scipy.special import airy

print(r'''
====================================================================
 STEP 11 | CONDITIONAL NAST-CONVENTION WC CORRECTION
====================================================================

This step first examines the raw weak-coupling probability.

If raw P_WC never reaches 1:
  - no NAST-prefixed WC variables are produced
  - no WC rates are recomputed
  - no plots are generated
  - no files are saved

If raw P_WC >= 1:
  - NAST convention is applied
  - P_WC is set to zero from the first unphysical point onward
  - WC probabilities, microcanonical rates, canonical rates,
    intermediate channels, and symmetry-collapsed MS channels are
    recomputed with NAST_ prefixes

For MS-specific channels, every nonzero channel is treated independently.
Channels with identical |H_SO| values have identical corrected WC
probabilities and rates, so only one representative curve is retained.
No MS-specific probabilities, numbers of states, microcanonical rates,
or canonical rates are summed.
''')

required_vars_step105 = [
    "jobname",
    "E_bins_cm1",
    "E_MECP",
    "H_SO_cm",
    "autocm",
    "mu_au",
    "gradmean",
    "DeltaF_parallel",
    "compute_rate_from_probability_WC",
    "probWC_eff",
    "rateWC_eff",
    "nosWC_eff",
    "E_rate_WC_cm1",
    "rho_reactant_cm",
    "local_base",
    "remote_base",
]

for var in required_vars_step105:
    if var not in globals():
        raise RuntimeError(f"{var} is missing. Run Steps 8–10 first.")

RUN_MODE = globals().get("RUN_MODE", "CLUSTER")
workflow_mode = globals().get("workflow_mode", "MECP_ONLY")


def section(title):
    print("\n" + "=" * 72)
    print(f" {title}")
    print("=" * 72 + "\n")


def write_local_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def save_current_figure(path):
    plt.tight_layout()
    plt.savefig(path, dpi=300)
    plt.show()


def ms_tex_value(x):
    x = float(x)

    if abs(x - round(x)) < 1.0e-8:
        return str(int(round(x)))

    if abs(abs(x) - 0.5) < 1.0e-8:
        return r"\frac{1}{2}" if x > 0 else r"-\frac{1}{2}"

    if abs(abs(x) - 1.5) < 1.0e-8:
        return r"\frac{3}{2}" if x > 0 else r"-\frac{3}{2}"

    if abs(abs(x) - 2.5) < 1.0e-8:
        return r"\frac{5}{2}" if x > 0 else r"-\frac{5}{2}"

    if abs(abs(x) - 3.5) < 1.0e-8:
        return r"\frac{7}{2}" if x > 0 else r"-\frac{7}{2}"

    return f"{x:.1f}"


def make_NAST_group_legend(prefix, group_data):
    """
    Build a legend for one representative NAST-corrected MS-specific curve.

    Multiple symbols indicate channels with identical |H_SO| values and
    therefore identical corrected probabilities and rates. Commas indicate
    coincident curves; no summation is implied.
    """
    symbols = group_data.get("channel_symbols", [])
    H_abs = float(group_data["H_abs_cm1"])

    rate_symbols = [sym.replace("$P", "$k") for sym in symbols]

    if len(rate_symbols) == 0:
        return f"{prefix} MS |H|={H_abs:.1f}"

    if len(rate_symbols) <= 3:
        return (
            f"{prefix} "
            + ", ".join(rate_symbols)
            + f", |H|={H_abs:.1f}"
        )

    return (
        f"{prefix} "
        + ", ".join(rate_symbols[:3])
        + f", ..., |H|={H_abs:.1f}, "
        + f"equivalent channels={len(rate_symbols)}"
    )


section("RAW WC PROBABILITY CHECK")

E_bins_cm1 = np.asarray(E_bins_cm1, dtype=float).reshape(-1)
E_rate_WC_cm1 = np.asarray(E_rate_WC_cm1, dtype=float).reshape(-1)
rho_reactant_cm = np.asarray(rho_reactant_cm, dtype=float).reshape(-1)


def compute_WC_raw_for_HSO_cm(H_cm, E_grid_cm1):
    E_grid_cm1 = np.asarray(E_grid_cm1, dtype=float).reshape(-1)

    H_Eh = float(abs(H_cm)) / float(autocm)
    E_excess_Eh = (E_grid_cm1 - float(E_MECP)) / float(autocm)

    P_raw = np.zeros_like(E_grid_cm1, dtype=float)
    airy_arg = np.full_like(E_grid_cm1, np.nan, dtype=float)
    Ai = np.full_like(E_grid_cm1, np.nan, dtype=float)

    mask = np.isfinite(E_grid_cm1) & np.isfinite(E_excess_Eh)

    prefactor = (
        4.0
        * np.pi**2
        * H_Eh**2
        * (
            2.0 * float(mu_au)
            / (float(gradmean) * float(DeltaF_parallel))
        ) ** (2.0 / 3.0)
    )

    scale = (
        2.0
        * float(mu_au)
        * float(DeltaF_parallel)**2
        / float(gradmean)**4
    ) ** (1.0 / 3.0)

    airy_arg[mask] = -E_excess_Eh[mask] * scale
    Ai[mask] = airy(airy_arg[mask])[0]

    P_raw[mask] = prefactor * Ai[mask] ** 2
    P_raw[~np.isfinite(P_raw)] = 0.0

    return P_raw, airy_arg, Ai


def apply_NAST_WC_convention(P_raw, E_grid_cm1, threshold=1.0):
    P_raw = np.asarray(P_raw, dtype=float).reshape(-1)
    E_grid_cm1 = np.asarray(E_grid_cm1, dtype=float).reshape(-1)

    if P_raw.size != E_grid_cm1.size:
        raise RuntimeError("P_raw and E_grid_cm1 must have the same length.")

    P_NAST = np.copy(P_raw)

    bad = np.where(P_raw >= float(threshold))[0]

    first_bad_index = None
    first_bad_energy = None
    first_bad_value = None
    correction_needed = False

    if bad.size > 0:
        correction_needed = True
        first_bad_index = int(bad[0])
        first_bad_energy = float(E_grid_cm1[first_bad_index])
        first_bad_value = float(P_raw[first_bad_index])
        P_NAST[first_bad_index:] = 0.0

    P_NAST[~np.isfinite(P_NAST)] = 0.0
    P_NAST = np.clip(P_NAST, 0.0, 1.0)

    return {
        "P_NAST": P_NAST,
        "correction_needed": correction_needed,
        "first_bad_index": first_bad_index,
        "first_bad_energy": first_bad_energy,
        "first_bad_value": first_bad_value,
        "threshold": float(threshold),
    }


P_WC_raw_effective, airy_arg_WC_raw_effective, Ai_WC_raw_effective = (
    compute_WC_raw_for_HSO_cm(H_SO_cm, E_bins_cm1)
)

NAST_effective_result = apply_NAST_WC_convention(
    P_WC_raw_effective,
    E_bins_cm1,
    threshold=1.0
)

WC_NAST_correction_needed = NAST_effective_result["correction_needed"]
WC_NAST_first_bad_index = NAST_effective_result["first_bad_index"]
WC_NAST_first_bad_energy = NAST_effective_result["first_bad_energy"]
WC_NAST_first_bad_value = NAST_effective_result["first_bad_value"]

globals().update({
    "compute_WC_raw_for_HSO_cm": compute_WC_raw_for_HSO_cm,
    "apply_NAST_WC_convention": apply_NAST_WC_convention,
    "WC_NAST_correction_needed": WC_NAST_correction_needed,
    "WC_NAST_first_bad_index": WC_NAST_first_bad_index,
    "WC_NAST_first_bad_energy": WC_NAST_first_bad_energy,
    "WC_NAST_first_bad_value": WC_NAST_first_bad_value,
})

if not WC_NAST_correction_needed:

    print("Raw WC probability never reached 1.")
    print("NAST WC correction is NOT needed.")
    print("No NAST-prefixed variables were created.")
    print("No WC recomputation, plotting, saving, or uploading was performed.")
    print(f"Maximum raw P_WC = {np.nanmax(P_WC_raw_effective):.12e}")

    print("\nAvailable diagnostic flags:")
    print("  WC_NAST_correction_needed")
    print("  WC_NAST_first_bad_index")
    print("  WC_NAST_first_bad_energy")
    print("  WC_NAST_first_bad_value")
    print("  compute_WC_raw_for_HSO_cm")
    print("  apply_NAST_WC_convention")

    print("\n================ STEP 11 DONE: NO NAST WC CORRECTION NEEDED =================\n")

else:

    section("STEP 11 DIRECTORY SETUP")

    local_step105 = os.path.join(local_base, "NAST_WC_correction")
    os.makedirs(local_step105, exist_ok=True)

    remote_step105 = posixpath.join(remote_base, "NAST_WC_correction")

    if "remote_mkdir_p" in globals() and "sftp" in globals():
        try:
            remote_mkdir_p(sftp, remote_step105)
        except Exception:
            pass

    plot_files_step105 = {}

    def register_plot(name):
        path = os.path.join(local_step105, f"{jobname}_Step10p5_{name}.png")
        plot_files_step105[name] = path
        return path

    print(f"Local Step 11 directory  : {local_step105}")
    print(f"Remote Step 11 directory : {remote_step105}")

    section("EFFECTIVE WC NAST CONVENTION")

    print("NAST convention IS needed for effective WC.")
    print(f"  First raw P_WC >= 1 index  : {WC_NAST_first_bad_index}")
    print(f"  First raw P_WC >= 1 energy : {WC_NAST_first_bad_energy:.6f} cm^-1")
    print(f"  First raw P_WC >= 1 value  : {WC_NAST_first_bad_value:.12e}")

    NAST_P_WC_effective = NAST_effective_result["P_NAST"]

    NAST_N_WC_effective, NAST_k_WC_effective = (
        compute_rate_from_probability_WC(NAST_P_WC_effective)
    )

    NAST_k_WC_effective_micro_s_inv = NAST_k_WC_effective
    NAST_rateWC_eff = NAST_k_WC_effective
    NAST_nosWC_eff = NAST_N_WC_effective

    print(f"Original Python max P_WC        : {np.nanmax(probWC_eff):.12e}")
    print(f"Raw unclipped max P_WC          : {np.nanmax(P_WC_raw_effective):.12e}")
    print(f"NAST max P_WC                   : {np.nanmax(NAST_P_WC_effective):.12e}")
    print(f"Original Python max k_WC        : {np.nanmax(rateWC_eff):.12e} s^-1")
    print(f"NAST max k_WC                   : {np.nanmax(NAST_k_WC_effective):.12e} s^-1")

    section("INTERMEDIATE WC NAST CONVENTION")

    NAST_P_WC_intermediate = {}
    NAST_N_WC_intermediate = {}
    NAST_k_WC_intermediate = {}

    if "k_WC_intermediate" in globals() and len(k_WC_intermediate) > 0:
        for key, data in k_WC_intermediate.items():
            H_int = float(data["H_int_cm1"])

            P_raw, airy_arg_raw, Ai_raw = compute_WC_raw_for_HSO_cm(
                H_int,
                E_bins_cm1
            )

            result = apply_NAST_WC_convention(P_raw, E_bins_cm1, threshold=1.0)
            P_NAST = result["P_NAST"]

            N_NAST, k_NAST = compute_rate_from_probability_WC(P_NAST)

            NAST_P_WC_intermediate[key] = {
                "H_int_cm1": H_int,
                "abs_Ms_low": float(data["abs_Ms_low"]),
                "degeneracy": int(data["degeneracy"]),
                "P_raw": P_raw,
                "P_NAST": P_NAST,
                "airy_arg_raw": airy_arg_raw,
                "Ai_raw": Ai_raw,
                "correction_needed": result["correction_needed"],
                "first_bad_index": result["first_bad_index"],
                "first_bad_energy": result["first_bad_energy"],
                "first_bad_value": result["first_bad_value"],
            }

            NAST_N_WC_intermediate[key] = N_NAST

            NAST_k_WC_intermediate[key] = {
                "H_int_cm1": H_int,
                "abs_Ms_low": float(data["abs_Ms_low"]),
                "degeneracy": int(data["degeneracy"]),
                "N_NAST": N_NAST,
                "k_NAST": k_NAST,
                "P_NAST": P_NAST,
                "P_raw": P_raw,
                "correction_needed": result["correction_needed"],
                "first_bad_index": result["first_bad_index"],
                "first_bad_energy": result["first_bad_energy"],
                "first_bad_value": result["first_bad_value"],
            }

        print(f"Intermediate WC curves corrected : {len(NAST_k_WC_intermediate)}")

        for key, data in sorted(
            NAST_k_WC_intermediate.items(),
            key=lambda x: x[1]["abs_Ms_low"]
        ):
            print(
                f"  {key:20s} | "
                f"k^{{{ms_tex_value(data['abs_Ms_low'])}}} | "
                f"SOC_int={data['H_int_cm1']:.3f} | "
                f"needed={data['correction_needed']} | "
                f"first_bad_E={data['first_bad_energy']}"
            )
    else:
        print("No k_WC_intermediate found. Intermediate correction skipped.")

    section("SYMMETRY-COLLAPSED MS-SPECIFIC WC NAST CONVENTION")

    NAST_P_WC_channel_groups = {}
    NAST_N_WC_channel_groups = {}
    NAST_k_WC_channel_groups = {}

    if "k_WC_channel_groups" in globals() and len(k_WC_channel_groups) > 0:
        for group_key, group_data in k_WC_channel_groups.items():
            H_abs = float(group_data["H_abs_cm1"])
            labels = list(group_data.get("labels", []))
            symbols = list(group_data.get("channel_symbols", []))
            representative_label = group_data.get("representative_label")

            P_raw, airy_arg_raw, Ai_raw = compute_WC_raw_for_HSO_cm(
                H_abs,
                E_bins_cm1
            )

            result = apply_NAST_WC_convention(
                P_raw,
                E_bins_cm1,
                threshold=1.0
            )
            P_NAST = result["P_NAST"]

            N_representative_NAST, k_representative_NAST = (
                compute_rate_from_probability_WC(P_NAST)
            )

            NAST_P_WC_channel_groups[group_key] = {
                "H_abs_cm1": H_abs,
                "degeneracy": int(
                    group_data.get("degeneracy", len(labels))
                ),
                "channel_symbols": symbols,
                "labels": labels,
                "representative_label": representative_label,
                "P_raw": P_raw,
                "P_NAST": P_NAST,
                "airy_arg_raw": airy_arg_raw,
                "Ai_raw": Ai_raw,
                "correction_needed": result["correction_needed"],
                "first_bad_index": result["first_bad_index"],
                "first_bad_energy": result["first_bad_energy"],
                "first_bad_value": result["first_bad_value"],
                "combination_rule": "representative_only_no_sum",
            }

            NAST_N_WC_channel_groups[group_key] = (
                N_representative_NAST
            )

            NAST_k_WC_channel_groups[group_key] = {
                "H_abs_cm1": H_abs,
                "degeneracy": int(
                    group_data.get("degeneracy", len(labels))
                ),
                "channel_symbols": symbols,
                "labels": labels,
                "representative_label": representative_label,
                "N_representative_NAST": N_representative_NAST,
                "k_representative_NAST": k_representative_NAST,
                "P_NAST": P_NAST,
                "P_raw": P_raw,
                "correction_needed": result["correction_needed"],
                "first_bad_index": result["first_bad_index"],
                "first_bad_energy": result["first_bad_energy"],
                "first_bad_value": result["first_bad_value"],
                "combination_rule": "representative_only_no_sum",
            }

        print(
            "Representative MS-specific WC curves corrected : "
            f"{len(NAST_k_WC_channel_groups)}"
        )

        for key, data in sorted(
            NAST_k_WC_channel_groups.items(),
            key=lambda x: x[1]["H_abs_cm1"],
            reverse=True
        ):
            print(
                f"  {key:16s} | "
                f"|H|={data['H_abs_cm1']:.3f} | "
                f"equivalent={data['degeneracy']} | "
                f"needed={data['correction_needed']} | "
                f"first_bad_E={data['first_bad_energy']} | "
                f"representative={data['representative_label']}"
            )
    else:
        print(
            "No k_WC_channel_groups found. "
            "Representative MS-specific correction skipped."
        )

    section("CANONICAL WC NAST CONVENTION")

    NAST_k_WC_canonical_s_inv = None
    NAST_Q_R_WC = None
    NAST_k_WC_intermediate_canonical = {}
    NAST_k_WC_channel_groups_canonical = {}

    if (
        "canonical_rate_from_microcanonical" in globals()
        and "T_grid_K" in globals()
        and "dE_WC" in globals()
    ):
        NAST_k_WC_canonical_s_inv, NAST_Q_R_WC = canonical_rate_from_microcanonical(
            E_rate_WC_cm1,
            rho_reactant_cm,
            NAST_k_WC_effective[1:],
            T_grid_K,
            dE_WC
        )

        for key, data in NAST_k_WC_intermediate.items():
            k_T, _ = canonical_rate_from_microcanonical(
                E_rate_WC_cm1,
                rho_reactant_cm,
                data["k_NAST"][1:],
                T_grid_K,
                dE_WC
            )

            NAST_k_WC_intermediate_canonical[key] = {
                "H_int_cm1": data["H_int_cm1"],
                "abs_Ms_low": data["abs_Ms_low"],
                "degeneracy": data["degeneracy"],
                "k_T": k_T,
                "correction_needed": data["correction_needed"],
                "first_bad_energy": data["first_bad_energy"],
            }

        for key, data in NAST_k_WC_channel_groups.items():
            k_T, _ = canonical_rate_from_microcanonical(
                E_rate_WC_cm1,
                rho_reactant_cm,
                data["k_representative_NAST"][1:],
                T_grid_K,
                dE_WC
            )

            NAST_k_WC_channel_groups_canonical[key] = {
                "H_abs_cm1": data["H_abs_cm1"],
                "degeneracy": data["degeneracy"],
                "channel_symbols": data["channel_symbols"],
                "labels": data["labels"],
                "representative_label": data["representative_label"],
                "combination_rule": "representative_only_no_sum",
                "k_T": k_T,
                "correction_needed": data["correction_needed"],
                "first_bad_energy": data["first_bad_energy"],
            }

        print("Canonical NAST-WC correction completed.")
        print(f"Original Python WC canonical max : {np.nanmax(k_WC_canonical_s_inv):.12e} s^-1")
        print(f"NAST-WC canonical max            : {np.nanmax(NAST_k_WC_canonical_s_inv):.12e} s^-1")

    else:
        print("Step 10 canonical variables not found. Canonical correction skipped.")

    section("SELECTED-ENERGY WC DIAGNOSTIC")

    print(
        " E/cm^-1     P_WC_raw        P_WC_python     NAST_P_WC      "
        "k_WC_python/s^-1     NAST_k_WC/s^-1"
    )
    print("-" * 110)

    selected_energies = [3000, 3500, 3560, 3566, 3600, 4000, 5000, 6000, 8000]

    for E_test in selected_energies:
        idx = int(round(float(E_test) / float(globals().get("Estep", 1.0))))

        if 0 <= idx < len(E_bins_cm1):
            print(
                f"{E_bins_cm1[idx]:8.3f}  "
                f"{P_WC_raw_effective[idx]:14.7e}  "
                f"{probWC_eff[idx]:14.7e}  "
                f"{NAST_P_WC_effective[idx]:14.7e}  "
                f"{rateWC_eff[idx]:18.7e}  "
                f"{NAST_k_WC_effective[idx]:18.7e}"
            )

    section("SAVING STEP 11 OUTPUTS")

    summary_file_step105 = os.path.join(
        local_step105,
        f"{jobname}_Step10p5_NAST_WC_summary.txt"
    )

    effective_grid_file_step105 = os.path.join(
        local_step105,
        f"{jobname}_Step10p5_NAST_WC_effective_grid.txt"
    )

    intermediate_file_step105 = os.path.join(
        local_step105,
        f"{jobname}_Step10p5_NAST_WC_intermediate_summary.txt"
    )

    channel_file_step105 = os.path.join(
        local_step105,
        f"{jobname}_Step10p5_NAST_WC_representative_channel_summary.txt"
    )

    summary_lines = []
    summary_lines.append("Step 11 NAST-convention WC correction")
    summary_lines.append(f"Run mode = {RUN_MODE}")
    summary_lines.append(f"Workflow mode = {workflow_mode}")
    summary_lines.append("")
    summary_lines.append("[EFFECTIVE]")
    summary_lines.append(f"H_SO_cm = {float(H_SO_cm):.12f}")
    summary_lines.append(f"E_MECP = {float(E_MECP):.12f}")
    summary_lines.append(f"WC_NAST_correction_needed = {WC_NAST_correction_needed}")
    summary_lines.append(f"WC_NAST_first_bad_index = {WC_NAST_first_bad_index}")
    summary_lines.append(f"WC_NAST_first_bad_energy = {WC_NAST_first_bad_energy}")
    summary_lines.append(f"WC_NAST_first_bad_value = {WC_NAST_first_bad_value}")
    summary_lines.append(f"Python_P_WC_max = {np.nanmax(probWC_eff):.12e}")
    summary_lines.append(f"Raw_P_WC_max = {np.nanmax(P_WC_raw_effective):.12e}")
    summary_lines.append(f"NAST_P_WC_max = {np.nanmax(NAST_P_WC_effective):.12e}")
    summary_lines.append(f"Python_k_WC_max = {np.nanmax(rateWC_eff):.12e}")
    summary_lines.append(f"NAST_k_WC_max = {np.nanmax(NAST_k_WC_effective):.12e}")
    summary_lines.append("")
    summary_lines.append("[CANONICAL]")
    if NAST_k_WC_canonical_s_inv is not None:
        summary_lines.append(f"Python_k_WC_canonical_max = {np.nanmax(k_WC_canonical_s_inv):.12e}")
        summary_lines.append(f"NAST_k_WC_canonical_max = {np.nanmax(NAST_k_WC_canonical_s_inv):.12e}")
    else:
        summary_lines.append("Canonical correction skipped.")
    summary_lines.append("")
    summary_lines.append("[COUNTS]")
    summary_lines.append(f"NAST_intermediate_count = {len(NAST_k_WC_intermediate)}")
    summary_lines.append(f"NAST_representative_channel_count = {len(NAST_k_WC_channel_groups)}")

    write_local_text(summary_file_step105, "\n".join(summary_lines) + "\n")

    grid_lines = []
    grid_lines.append(
        "E_cm1  P_WC_raw  P_WC_python  NAST_P_WC  "
        "N_WC_python  NAST_N_WC  k_WC_python_s^-1  NAST_k_WC_s^-1"
    )

    for i in range(len(E_bins_cm1)):
        grid_lines.append(
            f"{E_bins_cm1[i]:18.10f} "
            f"{P_WC_raw_effective[i]:18.10e} "
            f"{probWC_eff[i]:18.10e} "
            f"{NAST_P_WC_effective[i]:18.10e} "
            f"{nosWC_eff[i]:18.10e} "
            f"{NAST_N_WC_effective[i]:18.10e} "
            f"{rateWC_eff[i]:18.10e} "
            f"{NAST_k_WC_effective[i]:18.10e}"
        )

    write_local_text(effective_grid_file_step105, "\n".join(grid_lines) + "\n")

    intermediate_lines = []
    intermediate_lines.append("NAST WC intermediate correction summary")
    intermediate_lines.append("label  abs_Ms_low  degeneracy  H_int_cm1  needed  first_bad_E  first_bad_value  k_max")

    for key, data in sorted(
        NAST_k_WC_intermediate.items(),
        key=lambda x: x[1]["abs_Ms_low"]
    ):
        intermediate_lines.append(
            f"{key:20s} "
            f"{data['abs_Ms_low']:12.6f} "
            f"{data['degeneracy']:5d} "
            f"{data['H_int_cm1']:18.10f} "
            f"{str(data['correction_needed']):8s} "
            f"{str(data['first_bad_energy']):18s} "
            f"{str(data['first_bad_value']):18s} "
            f"{np.nanmax(data['k_NAST']):18.10e}"
        )

    write_local_text(intermediate_file_step105, "\n".join(intermediate_lines) + "\n")

    channel_lines = []
    channel_lines.append("NAST WC representative MS-specific correction summary; no summation")
    channel_lines.append("group_key  H_abs_cm1  equivalent_count  symbols  representative_label  needed  first_bad_E  first_bad_value  k_representative_max")

    for key, data in sorted(
        NAST_k_WC_channel_groups.items(),
        key=lambda x: x[1]["H_abs_cm1"],
        reverse=True
    ):
        channel_lines.append(
            f"{key:16s} "
            f"{data['H_abs_cm1']:18.10f} "
            f"{data['degeneracy']:5d} "
            f"{', '.join(data.get('channel_symbols', [])):35s} "
            f"{str(data.get('representative_label')):50s} "
            f"{str(data['correction_needed']):8s} "
            f"{str(data['first_bad_energy']):18s} "
            f"{str(data['first_bad_value']):18s} "
            f"{np.nanmax(data['k_representative_NAST']):18.10e}"
        )

    write_local_text(channel_file_step105, "\n".join(channel_lines) + "\n")

    section("PLOTTING NAST WC CORRECTION")

    plt.figure(figsize=(8.8, 5.0))
    plt.plot(E_bins_cm1, probWC_eff, linewidth=2.0, label="Python WC clipped")
    plt.plot(E_bins_cm1, P_WC_raw_effective, linewidth=1.4, linestyle=":", label="Raw WC unclipped")
    plt.plot(E_bins_cm1, NAST_P_WC_effective, linewidth=2.0, linestyle="--", label="NAST $P_{WC}$")
    plt.axhline(1.0, color="k", linestyle=":", linewidth=1.2, label="$P=1$")
    plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.2, label="MECP")
    plt.axvline(
        WC_NAST_first_bad_energy,
        color="k",
        linestyle="-.",
        linewidth=1.2,
        label="First raw $P_{WC}\\geq1$"
    )
    plt.xlim(max(0.0, float(E_MECP) - 1500.0), min(np.nanmax(E_bins_cm1), float(E_MECP) + 5000.0))
    plt.ylim(-0.05, 1.2)
    plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
    plt.ylabel("WC probability")
    plt.title("WC Probability: Python vs NAST Convention")
    plt.grid(False)
    plt.legend(fontsize=8)
    save_current_figure(register_plot("WC_probability_python_vs_NAST"))

    plt.figure(figsize=(8.8, 5.0))
    mask_py = rateWC_eff > 0.0
    mask_nast = NAST_k_WC_effective > 0.0
    plt.plot(
        E_bins_cm1[mask_py],
        np.log10(rateWC_eff[mask_py]),
        linewidth=2.0,
        label="Python $k_{WC}(E)$"
    )
    plt.plot(
        E_bins_cm1[mask_nast],
        np.log10(NAST_k_WC_effective[mask_nast]),
        linewidth=2.0,
        linestyle="--",
        label="NAST $k_{WC}(E)$"
    )
    plt.axvline(E_MECP, color="k", linestyle="--", linewidth=1.2, label="MECP")
    plt.axvline(
        WC_NAST_first_bad_energy,
        color="k",
        linestyle="-.",
        linewidth=1.2,
        label="First raw $P_{WC}\\geq1$"
    )
    plt.xlabel("Total energy relative to reference minimum (cm$^{-1}$)")
    plt.ylabel(r"$\log_{10}[k_{\mathrm{WC}}(E)]$")
    plt.title("WC Microcanonical Rate: Python vs NAST Convention")
    plt.grid(False)
    plt.legend(fontsize=8)
    save_current_figure(register_plot("WC_microcanonical_rate_python_vs_NAST"))

    if NAST_k_WC_canonical_s_inv is not None:
        plt.figure(figsize=(8.8, 5.0))
        plt.plot(
            T_grid_K,
            k_WC_canonical_s_inv,
            linewidth=2.0,
            label="Python $k_{WC}(T)$"
        )
        plt.plot(
            T_grid_K,
            NAST_k_WC_canonical_s_inv,
            linewidth=2.0,
            linestyle="--",
            label="NAST $k_{WC}(T)$"
        )
        plt.xlabel("Temperature (K)")
        plt.ylabel(r"$k_{\mathrm{WC}}(T)$ (s$^{-1}$)")
        plt.title("WC Canonical Rate: Python vs NAST Convention")
        plt.grid(False)
        plt.legend(fontsize=8)
        save_current_figure(register_plot("WC_canonical_rate_python_vs_NAST"))

        plt.figure(figsize=(8.8, 5.0))
        mask_py_T = k_WC_canonical_s_inv > 0.0
        mask_nast_T = NAST_k_WC_canonical_s_inv > 0.0
        plt.plot(
            1000.0 / T_grid_K[mask_py_T],
            np.log10(k_WC_canonical_s_inv[mask_py_T]),
            linewidth=2.0,
            label="Python $k_{WC}(T)$"
        )
        plt.plot(
            1000.0 / T_grid_K[mask_nast_T],
            np.log10(NAST_k_WC_canonical_s_inv[mask_nast_T]),
            linewidth=2.0,
            linestyle="--",
            label="NAST $k_{WC}(T)$"
        )
        plt.xlabel(r"$1000/T$ (K$^{-1}$)")
        plt.ylabel(r"$\log_{10}[k_{\mathrm{WC}}(T)]$")
        plt.title("Arrhenius-style WC Canonical Rate: Python vs NAST Convention")
        plt.grid(False)
        plt.legend(fontsize=8)
        save_current_figure(register_plot("WC_canonical_arrhenius_python_vs_NAST"))

    remote_summary_file_step105 = None
    remote_effective_grid_file_step105 = None
    remote_intermediate_file_step105 = None
    remote_channel_file_step105 = None
    remote_plot_files_step105 = {}

    if "sftp" in globals():
        try:
            remote_summary_file_step105 = posixpath.join(
                remote_step105,
                posixpath.basename(summary_file_step105)
            )
            remote_effective_grid_file_step105 = posixpath.join(
                remote_step105,
                posixpath.basename(effective_grid_file_step105)
            )
            remote_intermediate_file_step105 = posixpath.join(
                remote_step105,
                posixpath.basename(intermediate_file_step105)
            )
            remote_channel_file_step105 = posixpath.join(
                remote_step105,
                posixpath.basename(channel_file_step105)
            )

            sftp.put(summary_file_step105, remote_summary_file_step105)
            sftp.put(effective_grid_file_step105, remote_effective_grid_file_step105)
            sftp.put(intermediate_file_step105, remote_intermediate_file_step105)
            sftp.put(channel_file_step105, remote_channel_file_step105)

            for name, local_path in plot_files_step105.items():
                remote_path = posixpath.join(
                    remote_step105,
                    posixpath.basename(local_path)
                )
                sftp.put(local_path, remote_path)
                remote_plot_files_step105[name] = remote_path

        except Exception:
            remote_summary_file_step105 = None
            remote_effective_grid_file_step105 = None
            remote_intermediate_file_step105 = None
            remote_channel_file_step105 = None
            remote_plot_files_step105 = {}

    globals().update({
        "P_WC_raw_effective": P_WC_raw_effective,
        "airy_arg_WC_raw_effective": airy_arg_WC_raw_effective,
        "Ai_WC_raw_effective": Ai_WC_raw_effective,

        "NAST_P_WC_effective": NAST_P_WC_effective,
        "NAST_N_WC_effective": NAST_N_WC_effective,
        "NAST_k_WC_effective": NAST_k_WC_effective,
        "NAST_k_WC_effective_micro_s_inv": NAST_k_WC_effective_micro_s_inv,
        "NAST_rateWC_eff": NAST_rateWC_eff,
        "NAST_nosWC_eff": NAST_nosWC_eff,

        "NAST_P_WC_intermediate": NAST_P_WC_intermediate,
        "NAST_N_WC_intermediate": NAST_N_WC_intermediate,
        "NAST_k_WC_intermediate": NAST_k_WC_intermediate,

        "NAST_P_WC_channel_groups": NAST_P_WC_channel_groups,
        "NAST_N_WC_channel_groups": NAST_N_WC_channel_groups,
        "NAST_k_WC_channel_groups": NAST_k_WC_channel_groups,

        "NAST_k_WC_canonical_s_inv": NAST_k_WC_canonical_s_inv,
        "NAST_Q_R_WC": NAST_Q_R_WC,
        "NAST_k_WC_intermediate_canonical": NAST_k_WC_intermediate_canonical,
        "NAST_k_WC_channel_groups_canonical": NAST_k_WC_channel_groups_canonical,

        "local_step105": local_step105,
        "remote_step105": remote_step105,
        "plot_files_step105": plot_files_step105,
        "summary_file_step105": summary_file_step105,
        "effective_grid_file_step105": effective_grid_file_step105,
        "intermediate_file_step105": intermediate_file_step105,
        "channel_file_step105": channel_file_step105,
        "remote_summary_file_step105": remote_summary_file_step105,
        "remote_effective_grid_file_step105": remote_effective_grid_file_step105,
        "remote_intermediate_file_step105": remote_intermediate_file_step105,
        "remote_channel_file_step105": remote_channel_file_step105,
        "remote_plot_files_step105": remote_plot_files_step105,
    })

    section("STEP 11 SUMMARY")

    print(f"NAST WC correction needed       : {WC_NAST_correction_needed}")
    print(f"First raw P_WC >= 1 energy      : {WC_NAST_first_bad_energy}")
    print(f"Python max WC micro rate        : {np.nanmax(rateWC_eff):.6e} s^-1")
    print(f"NAST max WC micro rate          : {np.nanmax(NAST_k_WC_effective):.6e} s^-1")

    if NAST_k_WC_canonical_s_inv is not None:
        print(f"Python max WC canonical rate    : {np.nanmax(k_WC_canonical_s_inv):.6e} s^-1")
        print(f"NAST max WC canonical rate      : {np.nanmax(NAST_k_WC_canonical_s_inv):.6e} s^-1")

    print("\nImportant new variables:")
    print("  NAST_P_WC_effective")
    print("  NAST_k_WC_effective_micro_s_inv")
    print("  NAST_k_WC_canonical_s_inv")
    print("  NAST_k_WC_intermediate")
    print("  NAST_k_WC_channel_groups")
    print("  WC_NAST_correction_needed")
    print("  WC_NAST_first_bad_energy")

    print("\nSTEP 11 COMPLETED SUCCESSFULLY.\n")

#%% STEP 12. FINAL RESULTS REPORTING + NAST INPUT GENERATION

import os
import posixpath
import numpy as np

print("\n================ STEP 12: FINAL RESULTS REPORTING AND DIAGNOSTICS =================\n")

# ============================================================
# 12A. Helper functions
# ============================================================

hartree_to_cm = float(globals().get("hartree_to_cm", 219474.6313705))
hartree_to_kjmol = 2625.499638
cm_to_kjmol = 0.01196266

def section(title):
    line = "\n" + "=" * 72 + f"\n {title}\n" + "=" * 72 + "\n"
    print(line)
    return line

def fmt_scalar(x, digits=12):
    try:
        if x is None:
            return "None"
        x = float(x)
        if not np.isfinite(x):
            return "nan"
        return f"{x:.{digits}g}"
    except Exception:
        return str(x)

def as_array_or_none(*names):
    for name in names:
        if name in globals():
            arr = np.asarray(globals()[name], dtype=float).reshape(-1)
            if arr.size > 0:
                return arr, name
    return None, None

def scalar_or_none(*names):
    for name in names:
        if name in globals():
            try:
                return float(globals()[name]), name
            except Exception:
                pass
    return None, None

def energy_block(label, value_Eh):
    if value_Eh is None or not np.isfinite(value_Eh):
        return [
            f"{label}_hartree = nan",
            f"{label}_cm1 = nan",
            f"{label}_kJmol = nan",
        ]
    return [
        f"{label}_hartree = {value_Eh:.15f}",
        f"{label}_cm1 = {value_Eh * hartree_to_cm:.8f}",
        f"{label}_kJmol = {value_Eh * hartree_to_kjmol:.8f}",
    ]

def format_namelist_array(name, values, per_line=10):
    values = np.asarray(values, dtype=float).reshape(-1)
    chunks = []
    for i in range(0, len(values), per_line):
        chunk = ", ".join(f"{x:.8g}" for x in values[i:i+per_line])
        if i == 0:
            chunks.append(f"{name} = {chunk}" + ("," if i + per_line < len(values) else ""))
        else:
            chunks.append(f"       {chunk}" + ("," if i + per_line < len(values) else ""))
    return "\n".join(chunks)

def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def print_and_store(lines, text=""):
    print(text)
    lines.append(str(text))

# ============================================================
# 12B. Choose active WC variables
# ============================================================

use_NAST_WC = (
    "WC_NAST_correction_needed" in globals()
    and WC_NAST_correction_needed is True
    and "NAST_P_WC_effective" in globals()
    and "NAST_k_WC_effective_micro_s_inv" in globals()
)

if use_NAST_WC:
    active_probWC = NAST_P_WC_effective
    active_rateWC = NAST_k_WC_effective_micro_s_inv
    active_nosWC = NAST_N_WC_effective
    active_WC_label = "NAST-corrected WC"
else:
    active_probWC = probWC_eff
    active_rateWC = rateWC_eff
    active_nosWC = nosWC_eff
    active_WC_label = "Original WC"

step12_console_lines = []
debug_records = []

print_and_store(step12_console_lines, f"WC source: {active_WC_label}")

# ============================================================
# 12C. Canonical rates
# ============================================================

required_canonical_vars = [
    "T_grid_K",
    "k_LZ_canonical_s_inv",
    "k_WC_canonical_s_inv",
]

missing_canonical = [v for v in required_canonical_vars if v not in globals()]

if missing_canonical:
    print_and_store(step12_console_lines, "\nWARNING: Canonical Step 10 variables not found.")
    print_and_store(step12_console_lines, f"Missing: {missing_canonical}")
    active_k_WC_canonical = None

else:
    if use_NAST_WC and "NAST_k_WC_canonical_s_inv" in globals():
        active_k_WC_canonical = NAST_k_WC_canonical_s_inv
    else:
        active_k_WC_canonical = k_WC_canonical_s_inv

    print_and_store(step12_console_lines, "\n================ CANONICAL RATE CONSTANTS ================\n")
    print_and_store(step12_console_lines, f"WC canonical source: {active_WC_label}\n")

    header = " T/K        1000/T        k_LZ(T) / s^-1        log10[k_LZ]        k_WC(T) / s^-1        log10[k_WC]"
    print_and_store(step12_console_lines, header)
    print_and_store(step12_console_lines, "-" * 105)

    for T, k_lz, k_wc in zip(T_grid_K, k_LZ_canonical_s_inv, active_k_WC_canonical):
        log_lz = np.log10(k_lz) if k_lz > 0 else np.nan
        log_wc = np.log10(k_wc) if k_wc > 0 else np.nan

        line = (
            f"{T:8.2f}  "
            f"{1000.0/T:10.6f}  "
            f"{k_lz:18.10e}  "
            f"{log_lz:14.8f}  "
            f"{k_wc:18.10e}  "
            f"{log_wc:14.8f}"
        )
        print_and_store(step12_console_lines, line)

# ============================================================
# 12D. Interactive microcanonical diagnostic
# ============================================================

required_micro_vars = [
    "E_bins_cm1",
    "Estep",
    "binX",
    "E_MECP",
    "dosR",
    "dosX",
    "dosTP",
    "probLZ_eff",
    "nosLZ_eff",
    "rateLZ_eff",
]

missing_micro = [v for v in required_micro_vars if v not in globals()]
if missing_micro:
    raise RuntimeError(f"Missing microcanonical variables: {missing_micro}")

print_and_store(step12_console_lines, "\n================ INTERACTIVE MICROCANONICAL DIAGNOSTIC ================\n")
print(f"Active MECP barrier          : {VaG_MECP_cm1:.6f} cm^-1\n")
print_and_store(step12_console_lines, "Enter an energy in cm^-1 to print LZ/WC probabilities and rates.")
print_and_store(step12_console_lines, "Type 'stop' to finish.\n")

while True:
    ans = input("Energy in cm^-1, or 'stop': ").strip()

    if ans.lower() in ["stop", "s", "q", "quit", "exit"]:
        print_and_store(step12_console_lines, "\nInteractive diagnostic stopped.")
        break

    try:
        E_debug_cm1 = float(ans)
    except ValueError:
        print("Please enter a valid number or 'stop'.")
        continue

    i_dbg = int(round(E_debug_cm1 / Estep))

    if i_dbg < 0 or i_dbg >= len(E_bins_cm1):
        msg = (
            f"Energy is outside grid. Allowed range: "
            f"{E_bins_cm1[0]:.3f} to {E_bins_cm1[-1]:.3f} cm^-1."
        )
        print(msg)
        continue

    if i_dbg > binX and (i_dbg - binX) < len(dosTP):
        dosTP_val = dosTP[i_dbg - binX]
    else:
        dosTP_val = np.nan

    rec = {
        "requested_energy_cm1": E_debug_cm1,
        "E_bin_cm1": float(E_bins_cm1[i_dbg]),
        "bin_index": int(i_dbg),
        "binX": int(binX),
        "E_MECP_cm1": float(E_MECP),
        "WC_source": active_WC_label,
        "dosR": float(dosR[i_dbg]),
        "dosX_shifted": float(dosX[i_dbg]),
        "dosTP_internal": float(dosTP_val),
        "P_LZ": float(probLZ_eff[i_dbg]),
        "P_WC": float(active_probWC[i_dbg]),
        "N_LZ": float(nosLZ_eff[i_dbg]),
        "N_WC": float(active_nosWC[i_dbg]),
        "k_LZ_s_inv": float(rateLZ_eff[i_dbg]),
        "k_WC_s_inv": float(active_rateWC[i_dbg]),
        "log10_k_LZ": float(np.log10(rateLZ_eff[i_dbg])) if rateLZ_eff[i_dbg] > 0 else np.nan,
        "log10_k_WC": float(np.log10(active_rateWC[i_dbg])) if active_rateWC[i_dbg] > 0 else np.nan,
    }

    if use_NAST_WC and "P_WC_raw_effective" in globals():
        rec["P_WC_raw"] = float(P_WC_raw_effective[i_dbg])

    debug_records.append(rec)

    block = []
    block.append(f"\n================ MICROCANONICAL COMPARISON AT {rec['E_bin_cm1']:.3f} cm^-1 ================")
    block.append(f"E bin                             = {rec['E_bin_cm1']:.3f} cm^-1")
    block.append(f"requested energy                  = {rec['requested_energy_cm1']:.3f} cm^-1")
    block.append(f"bin index                         = {rec['bin_index']}")
    block.append(f"binX                              = {rec['binX']}")
    block.append(f"MECP                              = {rec['E_MECP_cm1']:.6f} cm^-1")
    block.append(f"WC source                         = {rec['WC_source']}")
    block.append("\nDOS:")
    block.append(f"dosR                              = {rec['dosR']:.15e}")
    block.append(f"dosX shifted                      = {rec['dosX_shifted']:.15e}")
    block.append(f"dosTP internal                    = {rec['dosTP_internal']:.15e}")
    block.append("\nEffective probabilities:")
    block.append(f"P_LZ                              = {rec['P_LZ']:.15e}")
    block.append(f"P_WC                              = {rec['P_WC']:.15e}")
    if "P_WC_raw" in rec:
        block.append(f"P_WC_raw                          = {rec['P_WC_raw']:.15e}")
    block.append("\nEffective number of states:")
    block.append(f"N_LZ                              = {rec['N_LZ']:.15e}")
    block.append(f"N_WC                              = {rec['N_WC']:.15e}")
    block.append("\nMicrocanonical rates:")
    block.append(f"k_LZ                              = {rec['k_LZ_s_inv']:.15e} s^-1")
    block.append(f"k_WC                              = {rec['k_WC_s_inv']:.15e} s^-1")
    block.append(f"log10(k_LZ)                       = {rec['log10_k_LZ']:.9f}")
    block.append(f"log10(k_WC)                       = {rec['log10_k_WC']:.9f}")
    block.append("")

    for line in block:
        print_and_store(step12_console_lines, line)

# ============================================================
# 12E. Final output directory
# ============================================================

final_output_dir = os.path.join(local_base, "final output and NAST input")
os.makedirs(final_output_dir, exist_ok=True)

remote_final_output_dir = None
if "remote_base" in globals():
    remote_final_output_dir = posixpath.join(remote_base, "final output and NAST input")
    if "remote_mkdir_p" in globals() and "sftp" in globals():
        try:
            remote_mkdir_p(sftp, remote_final_output_dir)
        except Exception:
            remote_final_output_dir = None

# ============================================================
# 12F. Gather final physical quantities
# ============================================================

freR, freR_source = as_array_or_none("freR_cm1", "freq_reactant_real_cm1")
freX, freX_source = as_array_or_none("freq_MECP_effhess_real_cm1", "freX_cm1", "freq_MECP_eff_real_cm1")

inertR, inertR_source = as_array_or_none("inertR", "inertR_amu_bohr2", "Reference_moments_of_inertia_amu_bohr2", "Reference_rot_constants_cm1", "inertR_rot_constants_cm1")
inertX, inertX_source = as_array_or_none("inertX", "inertX_amu_bohr2", "MECP_moments_of_inertia_amu_bohr2", "MECP_rot_constants_cm1", "inertX_rot_constants_cm1")

redmass, redmass_source = scalar_or_none("reduced_mass_amu")
soc_eff, soc_source = scalar_or_none("H_SO_cm")
grad, grad_source = scalar_or_none("DELTAF_PARALLEL_EH_PER_BOHR", "DeltaF_parallel")
gradmean, gradmean_source = scalar_or_none("GRADMEAN_EH_PER_BOHR", "gradmean_Eh_per_Bohr")

Ele_R_abs_Eh, Ele_R_source = scalar_or_none("Ele_REF_hartree", "E_ref_hartree")
Ele_X_abs_Eh, Ele_X_source = scalar_or_none("Ele_MECP_hartree")

Ele_barrier_Eh, Ele_barrier_source = scalar_or_none("E_MECP_electronic_hartree")
if Ele_barrier_Eh is None and Ele_R_abs_Eh is not None and Ele_X_abs_Eh is not None:
    Ele_barrier_Eh = Ele_X_abs_Eh - Ele_R_abs_Eh
    Ele_barrier_source = "Ele_MECP_hartree - Ele_REF_hartree"

ZPE_R_Eh, ZPE_R_source = scalar_or_none("ZPE_REF_freq_hartree", "ZPE_REF_from_freq_hartree", "ZPE_REF_hartree", "ZPE_REF_thermo_hartree")

ZPE_X_Eh, ZPE_X_source = scalar_or_none(
    "ZPE_MECP_effhess_hartree",
    "ZPE_X_hartree",
    "ZPE_MECP_hartree",
    "ZPE_MECP_from_freq_hartree"
)

if ZPE_X_Eh is None and freX is not None:
    ZPE_X_Eh = 0.5 * float(np.sum(freX)) / hartree_to_cm
    ZPE_X_source = "0.5*sum(freX)/hartree_to_cm"

ZPE_corrected_barrier_Eh = None
if Ele_barrier_Eh is not None and ZPE_R_Eh is not None and ZPE_X_Eh is not None:
    ZPE_corrected_barrier_Eh = Ele_barrier_Eh + (ZPE_X_Eh - ZPE_R_Eh)

T1_nast = float(np.asarray(T_grid_K, dtype=float)[0]) if "T_grid_K" in globals() else 300.0
T2_nast = float(np.asarray(T_grid_K, dtype=float)[-1]) if "T_grid_K" in globals() else 350.0
Estep_nast = float(globals().get("Estep", 1.0))

# NAST maxn is determined from the maximum total energy selected in Step 7.
# E_max_cm1 = ZPE-corrected MECP barrier + maximum energy above the MECP.
if "E_max_cm1" not in globals():
    raise RuntimeError(
        "Cannot determine NAST maxn because E_max_cm1 from Step 7 is missing."
    )

if Estep_nast <= 0.0:
    raise RuntimeError("Estep must be greater than zero.")

maxn_nast = int(np.ceil(float(E_max_cm1) / Estep_nast))

# Actual maximum energy represented by the integer NAST grid
E_max_nast_cm1 = maxn_nast * Estep_nast

# ============================================================
# 12G. Final report
# ============================================================

report = []


def finite_min_max(values):
    """
    Return the minimum and maximum finite values in an array.

    NaN and infinite values are ignored. If no finite values are
    available, (nan, nan) is returned.
    """
    if values is None:
        return np.nan, np.nan

    arr = np.asarray(values, dtype=float).reshape(-1)
    finite = arr[np.isfinite(arr)]

    if finite.size == 0:
        return np.nan, np.nan

    return float(np.min(finite)), float(np.max(finite))


# ============================================================
# Probability and rate extrema
# These values are written only to FINAL_OUTPUT.txt.
# Nothing in this section is printed to the console.
# ============================================================

# Landau-Zener probability
P_LZ_min, P_LZ_max = finite_min_max(
    globals().get("probLZ_eff", None)
)

# Raw weak-coupling probability before any Step 11 correction
P_WC_raw_array = globals().get("P_WC_raw_effective", None)

if P_WC_raw_array is None:
    # Fallback for workflows where the original Step 8 WC probability
    # is stored only as probWC_eff.
    P_WC_raw_array = globals().get("probWC_eff", None)
    P_WC_raw_source = "probWC_eff"
else:
    P_WC_raw_source = "P_WC_raw_effective"

P_WC_raw_min, P_WC_raw_max = finite_min_max(P_WC_raw_array)

# Final weak-coupling probability used downstream
P_WC_final_min, P_WC_final_max = finite_min_max(active_probWC)

if use_NAST_WC:
    P_WC_final_source = "NAST-corrected WC probability from Step 11"
else:
    P_WC_final_source = "Original WC probability; no Step 11 correction applied"

# Microcanonical rates
k_LZ_micro_min, k_LZ_micro_max = finite_min_max(
    globals().get("rateLZ_eff", None)
)

k_WC_micro_min, k_WC_micro_max = finite_min_max(
    active_rateWC
)

# Canonical rates
k_LZ_canonical_min, k_LZ_canonical_max = finite_min_max(
    globals().get("k_LZ_canonical_s_inv", None)
)

k_WC_canonical_min, k_WC_canonical_max = finite_min_max(
    active_k_WC_canonical
)

# ============================================================
# Intermediate SOC values from Eq. (2)
# ============================================================

intermediate_soc_records = []

# Preferred source: intermediate SOC values already constructed in Step 8.
if (
    "P_LZ_intermediate" in globals()
    and isinstance(P_LZ_intermediate, dict)
    and P_LZ_intermediate
):
    for label, data in sorted(
        P_LZ_intermediate.items(),
        key=lambda item: float(item[1].get("abs_Ms_low", np.inf))
    ):
        intermediate_soc_records.append({
            "label": str(label),
            "abs_Ms_low": float(data["abs_Ms_low"]),
            "degeneracy": int(data.get("degeneracy", 1)),
            "H_int_cm1": float(data["H_int_cm1"]),
            "H_rows_cm1": np.asarray(
                data.get("H_rows_cm1", []),
                dtype=float
            ).reshape(-1),
            "source": "P_LZ_intermediate from Step 8",
        })

# Fallback: reconstruct the intermediate SOCs directly from the
# Ms-resolved SOC matrix using the same definition employed in Step 8.
elif (
    "SOC_Ms_matrix_cm1" in globals()
    and "Ms_low" in globals()
):
    soc_matrix_for_intermediate = np.asarray(
        SOC_Ms_matrix_cm1,
        dtype=complex
    )

    ms_low_for_intermediate = np.asarray(
        Ms_low,
        dtype=float
    ).reshape(-1)

    if soc_matrix_for_intermediate.shape[0] != len(ms_low_for_intermediate):
        raise RuntimeError(
            "Cannot construct intermediate SOC values because the number "
            "of SOC-matrix rows does not match the number of low-spin Ms values."
        )

    unique_abs_ms = sorted(
        set(round(abs(float(ms)), 8) for ms in ms_low_for_intermediate)
    )

    for abs_ms in unique_abs_ms:
        row_indices = [
            i
            for i, ms in enumerate(ms_low_for_intermediate)
            if abs(abs(float(ms)) - abs_ms) < 1.0e-8
        ]

        row_soc_values = []

        for i in row_indices:
            row = soc_matrix_for_intermediate[i, :]
            row_soc = float(
                np.sqrt(np.sum(np.abs(row) ** 2))
            )

            if np.isfinite(row_soc) and row_soc > 1.0e-12:
                row_soc_values.append(row_soc)

        row_soc_values = np.asarray(
            row_soc_values,
            dtype=float
        )

        if row_soc_values.size == 0:
            continue

        # Equivalent +Ms and -Ms rows are combined by their RMS,
        # following the Step 8 intermediate-SOC definition.
        H_int_cm1 = float(
            np.sqrt(np.mean(row_soc_values ** 2))
        )

        intermediate_soc_records.append({
            "label": f"INT_absMs{abs_ms:g}",
            "abs_Ms_low": float(abs_ms),
            "degeneracy": int(len(row_indices)),
            "H_int_cm1": H_int_cm1,
            "H_rows_cm1": row_soc_values,
            "source": "Reconstructed from SOC_Ms_matrix_cm1 in Step 12",
        })
# ============================================================
# Main final report
# ============================================================

report.append("FINAL SPARKS OUTPUT")
report.append("=" * 72)
report.append(f"jobname = {jobname}")
report.append(f"final_output_dir = {final_output_dir}")
report.append("")

report.append("[ALL MS-SPECIFIC SOC CHANNELS]")
if "H_SO_channels_cm1" in globals() and H_SO_channels_cm1:
    for key, value in H_SO_channels_cm1.items():
        report.append(
            f"{key:45s} = "
            f"{value.real: .10f} {value.imag:+.10f}i cm^-1 ; "
            f"|H| = {abs(value):.10f}"
        )
else:
    report.append("No H_SO_channels_cm1 available.")
report.append("")

report.append("[CLASSIFIED MS-SPECIFIC SOC MATRIX COMPONENTS]")
report.append(
    "These are individual complex SOC matrix elements classified by "
    "their spin-projection relationships;"
)

if (
    "H_SO_intermediate_components_cm1" in globals()
    and H_SO_intermediate_components_cm1
):
    for key, value in H_SO_intermediate_components_cm1.items():
        report.append(
            f"{key:20s} = "
            f"{value.real: .10f} {value.imag:+.10f}i cm^-1 ; "
            f"|H| = {abs(value):.10f}"
        )
else:
    report.append("No classified SOC matrix components available.")

report.append("")


report.append("[INTERMEDIATE SOC VALUES]")

if intermediate_soc_records:
    report.append(
        "Intermediate SOCs are calculated separately for each unique "
        "|Ms_low| value using Eq. (2)."
    )

    for rec in intermediate_soc_records:
        rows_text = ", ".join(
            f"{value:.10f}"
            for value in rec["H_rows_cm1"]
        )

        report.append(
            f"|Ms_low| = {rec['abs_Ms_low']:.6g} ; "
            f"degeneracy = {rec['degeneracy']} ; "
            f"SOC_int = {rec['H_int_cm1']:.10f} cm^-1"
        )

        if rows_text:
            report.append(
                f"  contributing row SOC values = [{rows_text}] cm^-1"
            )

        report.append(
            f"  source = {rec['source']}"
        )
else:
    report.append(
        "No intermediate SOC values are available. "
        "The workflow may be operating in effective-only SOC mode."
    )

report.append("")

report.append("[EFFECTIVE SOC]")
report.append(
    f"H_SO_cm = {fmt_scalar(soc_eff)} cm^-1 ; source = {soc_source}"
)
report.append(
    "H_SO_ORCA_effective_cm = "
    f"{fmt_scalar(globals().get('H_SO_ORCA_effective_cm', np.nan))} cm^-1"
)
report.append(
    f"H_SO_source = {globals().get('H_SO_source', 'not available')}"
)
report.append("")

report.append("[GRADIENT QUANTITIES]")
report.append(
    "grad / DELTAF_PARALLEL_EH_PER_BOHR = "
    f"{fmt_scalar(grad)} Eh/Bohr ; source = {grad_source}"
)
report.append(
    "gradmean / GRADMEAN_EH_PER_BOHR = "
    f"{fmt_scalar(gradmean)} Eh/Bohr ; source = {gradmean_source}"
)
report.append("")

report.append("[ROTATIONAL INERTIA / ROTATIONAL DATA]")
report.append(f"inertR source = {inertR_source}")
report.append(str(inertR))
report.append(f"inertX source = {inertX_source}")
report.append(str(inertX))
report.append("")

report.append("[REDUCED MASS]")
report.append(
    f"reduced_mass_amu = {fmt_scalar(redmass)} amu ; "
    f"source = {redmass_source}"
)
report.append("")

report.append("[FREQUENCIES]")
report.append(f"freR source = {freR_source}")
report.append(f"freR count = {0 if freR is None else len(freR)}")
report.append(str(freR))
report.append("")

report.append(f"freX source = {freX_source}")
report.append(f"freX count = {0 if freX is None else len(freX)}")
report.append(str(freX))
report.append("")

report.append("[ENERGIES]")
report += energy_block(
    "Electronic_energy_reactant_absolute",
    Ele_R_abs_Eh
)
report += energy_block(
    "Electronic_energy_MECP_absolute",
    Ele_X_abs_Eh
)
report += energy_block(
    "Electronic_barrier",
    Ele_barrier_Eh
)
report += energy_block(
    "ZPE_reactant",
    ZPE_R_Eh
)
report += energy_block(
    "ZPE_MECP",
    ZPE_X_Eh
)
report += energy_block(
    "ZPE_corrected_barrier",
    ZPE_corrected_barrier_Eh
)

report.append(f"Electronic barrier source = {Ele_barrier_source}")
report.append(f"ZPE reactant source = {ZPE_R_source}")
report.append(f"ZPE MECP source = {ZPE_X_source}")
report.append("")


# ============================================================
# Global probability and rate extrema
# ============================================================

report.append("[GLOBAL MINIMUM AND MAXIMUM PROBABILITIES AND RATES]")
report.append("")

report.append("Landau-Zener probability:")
report.append(f"P_LZ source = probLZ_eff")
report.append(f"Minimum P_LZ = {P_LZ_min:.15e}")
report.append(f"Maximum P_LZ = {P_LZ_max:.15e}")
report.append("")

report.append("Raw weak-coupling probability:")
report.append(f"P_WC_raw source = {P_WC_raw_source}")
report.append(f"Minimum P_WC_raw = {P_WC_raw_min:.15e}")
report.append(f"Maximum P_WC_raw = {P_WC_raw_max:.15e}")
report.append("")

report.append("Final weak-coupling probability used downstream:")
report.append(f"P_WC_final source = {P_WC_final_source}")
report.append(f"Minimum P_WC_final = {P_WC_final_min:.15e}")
report.append(f"Maximum P_WC_final = {P_WC_final_max:.15e}")
report.append("")

report.append("Microcanonical Landau-Zener rate constants:")
report.append(f"k_LZ_micro source = rateLZ_eff")
report.append(f"Minimum k_LZ_micro = {k_LZ_micro_min:.15e} s^-1")
report.append(f"Maximum k_LZ_micro = {k_LZ_micro_max:.15e} s^-1")
report.append("")

report.append("Microcanonical weak-coupling rate constants:")
report.append(f"k_WC_micro source = {active_WC_label}")
report.append(f"Minimum k_WC_micro = {k_WC_micro_min:.15e} s^-1")
report.append(f"Maximum k_WC_micro = {k_WC_micro_max:.15e} s^-1")
report.append("")

report.append("Canonical Landau-Zener rate constants:")
report.append(
    f"k_LZ_canonical source = k_LZ_canonical_s_inv"
)
report.append(
    f"Minimum k_LZ_canonical = {k_LZ_canonical_min:.15e} s^-1"
)
report.append(
    f"Maximum k_LZ_canonical = {k_LZ_canonical_max:.15e} s^-1"
)
report.append("")

report.append("Canonical weak-coupling rate constants:")
report.append(f"k_WC_canonical source = {active_WC_label}")
report.append(
    f"Minimum k_WC_canonical = {k_WC_canonical_min:.15e} s^-1"
)
report.append(
    f"Maximum k_WC_canonical = {k_WC_canonical_max:.15e} s^-1"
)
report.append("")


# ============================================================
# Existing Step 12 canonical and interactive output
# ============================================================

report.append("[CANONICAL AND INTERACTIVE STEP 12 OUTPUT]")
report.extend(step12_console_lines)
report.append("")

report.append("[SELECTED MICROCANONICAL ENERGY RECORDS]")
if debug_records:
    for rec in debug_records:
        report.append("")
        for key, value in rec.items():
            report.append(f"{key} = {value}")
else:
    report.append("No interactive energies were selected.")
report.append("")


# ============================================================
# Write final report
# ============================================================

final_report_text = "\n".join(report) + "\n"

final_report_file = os.path.join(
    final_output_dir,
    f"{jobname}_FINAL_OUTPUT.txt"
)

write_text(
    final_report_file,
    final_report_text
)

# ============================================================
# 12H. Write NAST input files
# ============================================================

if freR is None:
    raise RuntimeError("Cannot write NAST input: freR was not found.")
if freX is None:
    raise RuntimeError("Cannot write NAST input: freX was not found.")
if inertR is None:
    raise RuntimeError("Cannot write NAST input: inertR was not found.")
if inertX is None:
    raise RuntimeError("Cannot write NAST input: inertX was not found.")
if redmass is None:
    raise RuntimeError("Cannot write NAST input: reduced_mass_amu was not found.")
if soc_eff is None:
    raise RuntimeError("Cannot write NAST input: H_SO_cm was not found.")
if grad is None:
    raise RuntimeError("Cannot write NAST input: DELTAF_PARALLEL_EH_PER_BOHR was not found.")
if gradmean is None:
    raise RuntimeError("Cannot write NAST input: GRADMEAN_EH_PER_BOHR was not found.")
if Ele_barrier_Eh is None:
    raise RuntimeError("Cannot write NAST input: electronic MECP barrier was not found.")
if ZPE_corrected_barrier_Eh is None:
    raise RuntimeError("Cannot write NAST input: ZPE-corrected MECP barrier could not be constructed.")

def build_nast_input(zpe_value, enX_value):
    return f"""&keys
zpe = {int(zpe_value)}
printmore = .true.
&end

&inputdata
{format_namelist_array("freR", freR)}

{format_namelist_array("freX", freX)}

{format_namelist_array("inertR", inertR)}
{format_namelist_array("inertX", inertX)}

enR = 0.0
enX = {enX_value:.15f}

maxn = {maxn_nast}
Estep = {Estep_nast:.8g}
T1 = {T1_nast:.8g}
T2 = {T2_nast:.8g}
&end

&probability
redmass = {redmass:.15f}
soc = {soc_eff:.15f}
grad = {grad:.15e}
gradmean = {gradmean:.15e}
&end
"""

nast_zpe1_text = build_nast_input(
    zpe_value=1,
    enX_value=Ele_barrier_Eh
)

nast_zpe0_text = build_nast_input(
    zpe_value=0,
    enX_value=ZPE_corrected_barrier_Eh
)

nast_zpe1_file = os.path.join(final_output_dir, f"{jobname}_NAST_zpe1_electronic_barrier.inp")
nast_zpe0_file = os.path.join(final_output_dir, f"{jobname}_NAST_zpe0_ZPE_corrected_barrier.inp")

write_text(nast_zpe1_file, nast_zpe1_text)
write_text(nast_zpe0_file, nast_zpe0_text)

# Optional upload to cluster final_output folder
if remote_final_output_dir is not None and "sftp" in globals():
    try:
        sftp.put(final_report_file, posixpath.join(remote_final_output_dir, os.path.basename(final_report_file)))
        sftp.put(nast_zpe1_file, posixpath.join(remote_final_output_dir, os.path.basename(nast_zpe1_file)))
        sftp.put(nast_zpe0_file, posixpath.join(remote_final_output_dir, os.path.basename(nast_zpe0_file)))   
    except Exception as err:
        print("\nWARNING: Could not upload final output files to the cluster.")
        print(err)

globals().update({
    "final_output_dir": final_output_dir,
    "final_report_file": final_report_file,
    "nast_zpe1_file": nast_zpe1_file,
    "nast_zpe0_file": nast_zpe0_file,
    "debug_records_step12": debug_records,
    "ZPE_corrected_barrier_Eh_final": ZPE_corrected_barrier_Eh,
    "Electronic_barrier_Eh_final": Ele_barrier_Eh,
})

print("\nSTEP 12 COMPLETED SUCCESSFULLY.\n")

#%% SSH / SFTP reconnection utility

import paramiko


# ============================================================
# Helper functions
# ============================================================

def close_quiet(connection):
    """Close an SSH or SFTP connection without raising exceptions."""
    try:
        if connection is not None:
            connection.close()
    except Exception:
        pass


def ssh_connection_alive(ssh_connection):
    """Return True if the SSH transport is active."""
    try:
        transport = ssh_connection.get_transport()
        return transport is not None and transport.is_active()
    except Exception:
        return False


def sftp_connection_alive(sftp_connection):
    """Return True if the SFTP session is active."""
    try:
        sftp_connection.listdir(".")
        return True
    except Exception:
        return False


# ============================================================
# Connection check
# ============================================================

ssh_alive = ssh_connection_alive(ssh)
sftp_alive = sftp_connection_alive(sftp)

print(f"SSH connection status  : {'ACTIVE' if ssh_alive else 'DISCONNECTED'}")
print(f"SFTP connection status : {'ACTIVE' if sftp_alive else 'DISCONNECTED'}")

# ============================================================
# Automatic reconnection if necessary
# ============================================================

if ssh_alive and sftp_alive:

    print("\nCluster connection is already active.")
    print("No reconnection was necessary.")

else:

    print("\nCluster connection was lost.")
    print("Attempting automatic SSH/SFTP reconnection...\n")

    close_quiet(globals().get("sftp"))
    close_quiet(globals().get("ssh"))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh.connect(
        hostname=cluster_host,
        username=cluster_account,
        password=cluster_password,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
        look_for_keys=False,
        allow_agent=False
    )

    sftp = ssh.open_sftp()

    if not ssh_connection_alive(ssh):
        raise RuntimeError("SSH reconnection failed.")

    if not sftp_connection_alive(sftp):
        raise RuntimeError("SFTP reconnection failed.")

    globals()["ssh"] = ssh
    globals()["sftp"] = sftp

    print("SSH reconnection successful.")
    print("SFTP reconnection successful.")

