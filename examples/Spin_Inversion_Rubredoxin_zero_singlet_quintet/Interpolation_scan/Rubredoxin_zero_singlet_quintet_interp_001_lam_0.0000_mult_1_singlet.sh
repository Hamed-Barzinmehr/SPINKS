#!/bin/bash
#PBS -N Sing_Q_Zero_interp_001_lam_0.0000_mult_1_singlet
#PBS -l nodes=1:ppn=12
#PBS -o Sing_Q_Zero_interp_001_lam_0.0000_mult_1_singlet.pbs.out
#PBS -e Sing_Q_Zero_interp_001_lam_0.0000_mult_1_singlet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Sing_Q_Zero_interp_001_lam_0.0000_mult_1_singlet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Sing_Q_Zero_interp_001_lam_0.0000_mult_1_singlet.inp > Sing_Q_Zero_interp_001_lam_0.0000_mult_1_singlet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
