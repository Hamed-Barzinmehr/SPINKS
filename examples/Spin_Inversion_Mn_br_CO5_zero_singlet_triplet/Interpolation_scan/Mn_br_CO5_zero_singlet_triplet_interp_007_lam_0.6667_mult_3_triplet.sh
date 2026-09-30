#!/bin/bash
#PBS -N Mn_br_CO5_zero_singlet_triplet_interp_007_lam_0.6667_mult_3_triplet
#PBS -l nodes=1:ppn=12
#PBS -o Mn_br_CO5_zero_singlet_triplet_interp_007_lam_0.6667_mult_3_triplet.pbs.out
#PBS -e Mn_br_CO5_zero_singlet_triplet_interp_007_lam_0.6667_mult_3_triplet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Mn_br_CO5_zero_singlet_triplet_interp_007_lam_0.6667_mult_3_triplet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Mn_br_CO5_zero_singlet_triplet_interp_007_lam_0.6667_mult_3_triplet.inp > Mn_br_CO5_zero_singlet_triplet_interp_007_lam_0.6667_mult_3_triplet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
