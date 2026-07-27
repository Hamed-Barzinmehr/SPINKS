**Spin Probability And Reaction Kinetics Suite (SPINKS)**
**Current version:** 1.0.0 

SPINKS is a fully automated Python–ORCA computational framework for
end-to-end evaluation of spin-crossing probabilities and the corresponding
microcanonical and canonical reaction rate constants.

Starting from a single Cartesian molecular geometry, SPINKS automatically
performs the electronic-structure calculations and numerical analyses
required to obtain:

- optimized minima for two spin states;
- Cartesian interpolation between the optimized minima;
- minimum-energy crossing-point (MECP) optimization;
- spin–orbit coupling calculations;
- analytical energy gradients at the MECP;
- numerical Hessians and effective-Hessian analysis;
- reaction-coordinate construction and reduced-mass evaluation;
- Landau–Zener and Weak-Coupling spin-crossing probabilities;
- microcanonical reaction rate constants;
- canonical reaction rate constants;
- graphical and text-based outputs; and
- input files for independent validation with NAST.

## Developer

**Hamed Barzinmehr**  
Department of Chemistry and Biochemistry  
Baylor University

## Available Versions

SPINKS is distributed as two complete Python implementations that perform
the same scientific workflow. They differ only in how ORCA calculations are
executed and how files are managed.

### PC Version — Local ORCA Execution

`SPINKS_PC_version.py`

This version runs ORCA directly on the user's local computer and stores all
generated files in a local project directory.

### HPC Cluster Version — Remote ORCA Execution

`SPINKS_HPC_Cluster_version.py`

This version connects to a remote high-performance computing cluster through
SSH/SFTP, generates and submits batch jobs, monitors the calculations, and
retrieves the resulting files.
