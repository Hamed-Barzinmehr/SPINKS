#!/bin/bash
#PBS -N NiFe_hydrogenase_interp_003_lam_0.2222_mult_3_triplet
#PBS -l nodes=1:ppn=12
#PBS -o NiFe_hydrogenase_interp_003_lam_0.2222_mult_3_triplet.pbs.out
#PBS -e NiFe_hydrogenase_interp_003_lam_0.2222_mult_3_triplet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: NiFe_hydrogenase_interp_003_lam_0.2222_mult_3_triplet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca NiFe_hydrogenase_interp_003_lam_0.2222_mult_3_triplet.inp > NiFe_hydrogenase_interp_003_lam_0.2222_mult_3_triplet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
