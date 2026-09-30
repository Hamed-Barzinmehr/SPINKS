#!/bin/bash
#PBS -N Mn_byp_Br_CO3_zero_singlet_triplet_interp_004_lam_0.3333_mult_1_singlet
#PBS -l nodes=1:ppn=12
#PBS -o Mn_byp_Br_CO3_zero_singlet_triplet_interp_004_lam_0.3333_mult_1_singlet.pbs.out
#PBS -e Mn_byp_Br_CO3_zero_singlet_triplet_interp_004_lam_0.3333_mult_1_singlet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Mn_byp_Br_CO3_zero_singlet_triplet_interp_004_lam_0.3333_mult_1_singlet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Mn_byp_Br_CO3_zero_singlet_triplet_interp_004_lam_0.3333_mult_1_singlet.inp > Mn_byp_Br_CO3_zero_singlet_triplet_interp_004_lam_0.3333_mult_1_singlet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
