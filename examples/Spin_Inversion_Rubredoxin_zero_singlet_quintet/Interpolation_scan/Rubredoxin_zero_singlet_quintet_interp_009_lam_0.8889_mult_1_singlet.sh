#!/bin/bash
#PBS -N Rubredoxin_zero_singlet_quintet_interp_009_lam_0.8889_mult_1_singlet
#PBS -l nodes=1:ppn=12
#PBS -o Rubredoxin_zero_singlet_quintet_interp_009_lam_0.8889_mult_1_singlet.pbs.out
#PBS -e Rubredoxin_zero_singlet_quintet_interp_009_lam_0.8889_mult_1_singlet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Rubredoxin_zero_singlet_quintet_interp_009_lam_0.8889_mult_1_singlet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Rubredoxin_zero_singlet_quintet_interp_009_lam_0.8889_mult_1_singlet.inp > Rubredoxin_zero_singlet_quintet_interp_009_lam_0.8889_mult_1_singlet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
