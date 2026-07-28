#!/bin/bash
#PBS -N D_S_negative_1_interp_003_lam_0.2222_mult_2_doublet
#PBS -l nodes=1:ppn=12
#PBS -o D_S_negative_1_interp_003_lam_0.2222_mult_2_doublet.pbs.out
#PBS -e D_S_negative_1_interp_003_lam_0.2222_mult_2_doublet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: D_S_negative_1_interp_003_lam_0.2222_mult_2_doublet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca D_S_negative_1_interp_003_lam_0.2222_mult_2_doublet.inp > D_S_negative_1_interp_003_lam_0.2222_mult_2_doublet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
